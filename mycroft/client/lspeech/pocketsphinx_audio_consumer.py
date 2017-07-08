# Copyright 2016 Mycroft AI, Inc.
#
# This file is part of Mycroft Core.
#
# Mycroft Core is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Mycroft Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Mycroft Core.  If not, see <http://www.gnu.org/licenses/>.

from threading import Thread
import wave
import subprocess
from os.path import join, dirname, abspath, exists
import pyaudio
import time

from mycroft.configuration import ConfigurationManager
from mycroft.messagebus.message import Message
from mycroft.metrics import MetricsAggregator
from mycroft.session import SessionManager
from mycroft.util import check_for_signal
from mycroft.util.log import getLogger

from pocketsphinx import Decoder

logger = getLogger(__name__)
__author__ = 'SoloVeniaASaludar'

BASEDIR = dirname(abspath(__file__))


class PocketsphinxAudioConsumer(Thread):
    """
    PocketsphinxAudioConsumer
    Reads audio and produces utterances
    Based on local pocketsphinx
    """

    # In seconds, the minimum audio size to be sent to remote STT
    MIN_AUDIO_SIZE = 0.5

    def __init__(self, config_listener, lang, state, emitter):
        super(PocketsphinxAudioConsumer, self).__init__()
        self.SAMPLE_RATE = 16000
        self.SAMPLE_WIDTH = 2
        self.CHUNK = 1024
        self.config = config_listener
        self.lang = lang
        self.state = state
        self.emitter = emitter

        self.audio = pyaudio.PyAudio()

        self.forced_wake = False
        self.record_file = None
        self.wake_word = str(self.config.get(
             'wake_word', "Hey Mycroft")).lower()
        self.standup_word = str(self.config.get(
             'standup_word', "wake up")).lower()
        self.default_grammar = str(
             self.config.get('grammar', "lm"))
        self.grammar = self.default_grammar

        self.msg_awake = self.config.get('msg_awake', "I'm awake")
        self.msg_not_catch = self.config.get(
             'msg_not_catch', "Sorry, I didn't catch that")
        s = self.config.get('wake_word_ack_cmnd')
        self.wake_word_ack_cmnd = s.split(' ')

        self.metrics = MetricsAggregator()

        model_lang_dir = join(BASEDIR, 'model', str(self.lang))
        self.decoder = Decoder(self.create_decoder_config(model_lang_dir))
        self.decoder.set_keyphrase('wake_word', self.wake_word)
        jsgf = join(model_lang_dir, 'es.jsgf')
        if exists(jsgf):
            self.decoder.set_jsgf_file('jsgf', jsgf)
        lm = join(model_lang_dir, 'es.lm')
        if exists(lm):
            self.decoder.set_lm_file('lm', lm)

    def create_decoder_config(self, model_lang_dir):
        decoder_config = Decoder.default_config()
        hmm_dir = join(model_lang_dir, 'hmm')
        decoder_config.set_string('-hmm', join(model_lang_dir, hmm_dir))
        decoder_config.set_string('-dict', join(model_lang_dir, 'es.dict'))
        decoder_config.set_float('-samprate', self.SAMPLE_RATE)
        decoder_config.set_float('-kws_threshold', self.config.get('threshold', 1) )
        decoder_config.set_string('-cmninit', '40,3,-1') 
        decoder_config.set_string('-logfn', 'scripts/logs/decoder.log')
        return decoder_config

    def wake_word_ack(self):
        if self.wake_word_ack_cmnd: 
            subprocess.call( self.wake_word_ack_cmnd )

    def device_name_to_index(self, device_name):
        numdevices = self.audio.get_device_count()
        for device_index in range(0, numdevices):
            device = self.audio.get_device_info_by_index(device_index)
            if device_name == device.get('name'):
                return device_index
        return None

    def run(self):
        device_name = self.config.get("device_name")
        if device_name: 
            device_index = self.device_name_to_index(device_name)
        else:
            device_index = self.config.get("device_index")
        logger.debug("device_index=%s", device_index)

        self.stream = self.audio.open(
            input_device_index = device_index,
            channels=1,
            format= pyaudio.get_format_from_width(self.SAMPLE_WIDTH),
            rate=self.SAMPLE_RATE,
            frames_per_buffer=self.CHUNK,
            input=True,  # stream is an input stream
        )

        while self.state.running:
            # start new session
            SessionManager.touch()
            self.session = SessionManager.get().session_id

            wake_word_found = self.wait_until_wake_word()
            if wake_word_found:
                logger.debug("wake_word detected.")
                self.wake_word_ack()

                payload = {
                    'utterance': self.wake_word,
                    'session': self.session
                }
                context = {'session': self.session}
                self.emitter.emit("recognizer_loop:wakeword",
                                  payload, context)

            context = {'session': self.session}
            self.emitter.emit("recognizer_loop:record_begin", context)
            audio, text = self.record_phrase()
            self.emitter.emit("recognizer_loop:record_end", context)
            logger.debug("recorded.")

            if self.state.sleeping:
                self.standup(text)
            else:
                self.process(audio, text)

    def save_record( self, wav_name, audio ):
        # TODO: use "with"
        waveFile = wave.open(wav_name, 'wb')
        waveFile.setnchannels(1)
        waveFile.setsampwidth(self.SAMPLE_WIDTH)
        waveFile.setframerate(self.SAMPLE_RATE)
        waveFile.writeframes(audio)
        waveFile.close()

    def standup(self, text):
        if text and self.standup_word in text:
            SessionManager.touch()
            self.state.sleeping = False
            self.__speak(self.msg_awake)
            self.metrics.increment("mycroft.wakeup")

    def process(self, audio, text):

        # save this record in file if requested
        if self.record_file:
            self.save_record(self.record_file, audio)
            self.record_file = None

        if not self.grammar:
            # do not translate if only record is requested
            # recover default mode
            self.grammar = self.default_grammar
        elif text:
            # already translated in local recognizer
            payload = {
                'utterances': [text],
                'lang': self.lang,
            }
            context = {'session': self.session}
            self.emitter.emit("recognizer_loop:utterance", payload, context)
            self.metrics.attr('utterances', [text])
        else:
            logger.error("Speech Recognition could not understand audio")
            if self.msg_not_catch:
                self.__speak(self.msg_not_catch)

    def __speak(self, utterance):
        payload = {
            'utterance': utterance,
            'session': self.session
        }
        self.emitter.emit("speak", Message("speak", payload))

    #
    # ResponsiveRecognizer
    #

    # The maximum audio in seconds to keep for transcribing a phrase
    # The wake word must fit in this time
    SAVED_WW_SEC = 1.0

    # The maximum length a phrase can be recorded,
    # provided there is noise the entire time
    RECORDING_TIMEOUT = 10.0

    # Time between pocketsphinx checks for the wake word
    SEC_BETWEEN_WW_CHECKS = 0.2

    def record_sound_chunk(self):
        # TODO: if muted
        return self.stream.read(self.CHUNK)

    def record_phrase(self):
        logger.debug("Waiting for command: grammar=%s ...", self.grammar)

        # Maximum number of chunks to record before timing out
        sec_per_buffer = float(self.CHUNK) / self.SAMPLE_RATE
        max_chunks = int(self.RECORDING_TIMEOUT / sec_per_buffer)

        # bytearray to store audio in
        byte_data = ""

        num_chunks = 0
        in_speech = False
        hypstr = None
        if self.grammar:
            self.decoder.set_search(self.grammar)
        utt_running = False

        self.stream.start_stream()

        while num_chunks < max_chunks:
            chunk = self.record_sound_chunk()
            byte_data += chunk
            num_chunks += 1

            if not self.grammar:
                # no stt, only record
                continue

            if not utt_running:
                self.decoder.start_utt()
                utt_running = True

            self.decoder.process_raw(chunk, False, False)

            if self.decoder.get_in_speech():
                # voice
                if not in_speech:
                    logger.debug("silence->voice")
                    in_speech = True
            elif in_speech:
                # voice->silence
                logger.debug("voice->silence")
                in_speech = False

                self.decoder.end_utt()
                utt_running = False

                hyp = self.decoder.hyp()
                if hyp and hyp.hypstr:
                    hypstr = hyp.hypstr
                    logger.debug("hyp=%s", hypstr)
                    break
                logger.debug("false speech, discarded")

        if utt_running:
            self.decoder.end_utt()
            utt_running = False

        self.stream.stop_stream()

        return (byte_data, hypstr)

    def wait_until_wake_word(self):

        utt_running = False
        byte_data = ""
        wake_word_found = False
        num_chunks = 0

        self.decoder.set_search('wake_word')

        self.stream.start_stream()

        logger.debug("Waiting for wake word...")
        while not wake_word_found:
	    debug = self.config.get("debug",False)
 
            if self.forced_wake or check_for_signal('buttonPress'):
                logger.debug("Forced wake word...")
                self.forced_wake = False
                break

            chunk = self.record_sound_chunk()
            num_chunks += 1
            
            if debug:
                if num_chunks % 256 == 255:
                    filename = "/tmp/mycroft.wake.%f"%time.time()
                    logger.debug("record saved %s", filename)
                    self.save_record(filename, byte_data)
                    byte_data = ""
                else:
                    byte_data += chunk

            if not utt_running:
                self.decoder.start_utt()
                utt_running = True

            self.decoder.process_raw(chunk, False, False)

            hyp = self.decoder.hyp()
            if hyp and hyp.hypstr:
                logger.debug("hypstr=%s", hyp.hypstr)
                wake_word_found = (self.wake_word in hyp.hypstr)

        if utt_running:
            self.decoder.end_utt()

        self.stream.stop_stream()

        return wake_word_found

    def record(self, msg):
        self.forced_wake = True
        self.record_file = msg.data.get("record_filename")
        self.grammar = msg.data.get("grammar", self.default_grammar)
        self.session = msg.context.get("session")
