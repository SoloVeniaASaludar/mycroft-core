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


import re
import sys
from threading import Thread, Lock

from mycroft.client.enclosure.api import EnclosureAPI
from mycroft.client.lspeech.listener import RecognizerLoop
from mycroft.configuration import ConfigurationManager
from mycroft.identity import IdentityManager
from mycroft.messagebus.client.ws import WebsocketClient
from mycroft.messagebus.message import Message
from mycroft.tts import TTSFactory
from mycroft.util import kill, create_signal
from mycroft.util.log import getLogger
from mycroft.lock import Lock as PIDLock  # Create/Support PID locking file

logger = getLogger("SpeechClient")
ws = None
tts = TTSFactory.create()
lock = Lock()
loop = None

config = ConfigurationManager.get()


def handle_wakeword(event, context=None):
    logger.info("Wakeword Detected: " + event['utterance'])
    ws.emit(Message('recognizer_loop:wakeword', event, context=context))


def handle_utterance(event, context=None):
    logger.info("Utterance: " + str(event['utterances']))
    ws.emit(Message('recognizer_loop:utterance', event, context=context))


def mute_and_speak(utterance, in_msg=None):
    lock.acquire()
    if in_msg:
        msg = in_msg.reply("recognizer_loop:audio_output_start", {})
    else:
        msg = Message("recognizer_loop:audio_output_start")
    ws.emit(msg)

    try:
        logger.info("Speak: " + utterance)
        loop.mute()
        tts.execute(utterance)
    finally:
        loop.unmute()
        lock.release()
        if in_msg:
            msg = in_msg.reply("recognizer_loop:audio_output_end", {})
        else:
            msg = Message("recognizer_loop:audio_output_end")
        ws.emit(msg)


def handle_multi_utterance_intent_failure(event):
    logger.info("Failed to find intent on multiple intents.")
    # TODO: Localize
    mute_and_speak("Sorry, I didn't catch that. Please rephrase your request.")


def handle_speak(event):
    utterance = event.data['utterance']
    expect_response = event.data.get('expect_response', False)

    # This is a bit of a hack for Picroft.  The analog audio on a Pi blocks
    # for 30 seconds fairly often, so we don't want to break on periods
    # (decreasing the chance of encountering the block).  But we will
    # keep the split for non-Picroft installs since it give user feedback
    # faster on longer phrases.
    #
    # TODO: Remove or make an option?  This is really a hack, anyway,
    # so we likely will want to get rid of this when not running on Mimic
    if not config.get('enclosure', {}).get('platform') == "picroft":
        chunks = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s',
                          utterance)
        for chunk in chunks:
            try:
                mute_and_speak(chunk, event)
            except:
                logger.error('Error in mute_and_speak', exc_info=True)
    else:
        mute_and_speak(utterance, event)

    if expect_response:
        create_signal('buttonPress')


def handle_record(event):
    loop.record(event)


def handle_record_begin(context=None):
    logger.info("Begin Recording...")
    ws.emit(Message('recognizer_loop:record_begin', context=context))


def handle_record_end(context=None):
    logger.info("End Recording...")
    ws.emit(Message('recognizer_loop:record_end', context=context))


def handle_sleep(event):
    loop.sleep()


def handle_wake_up(event):
    loop.awaken()


def handle_stop(event):
    kill([config.get('tts').get('module')])
    kill(["aplay"])


def handle_paired(event):
    IdentityManager.update(event.data)


def handle_open():
    # Reset the UI to indicate ready for speech processing
    EnclosureAPI(ws).reset()


def connect():
    ws.run_forever()


def main():
    global ws
    global loop
    lock = PIDLock("voice")
    ws = WebsocketClient()
    tts.init(ws)

    ConfigurationManager.init(ws)
    listener_config = config.get('listener')
    for i in range(1, len(sys.argv)-1, 2):
        k = sys.argv[i]
        v = sys.argv[i+1]
        if k == 'device_index':
            listener_config[k]=int(v)

    loop = RecognizerLoop()
    loop.on('recognizer_loop:utterance', handle_utterance)
    loop.on('recognizer_loop:record_begin', handle_record_begin)
    loop.on('recognizer_loop:wakeword', handle_wakeword)
    loop.on('recognizer_loop:record_end', handle_record_end)
    loop.on('speak', handle_speak)

    ws.on('open', handle_open)
    ws.on('speak', handle_speak)
    ws.on('record', handle_record)
    ws.on(
        'multi_utterance_intent_failure',
        handle_multi_utterance_intent_failure)
    ws.on('recognizer_loop:sleep', handle_sleep)
    ws.on('recognizer_loop:wake_up', handle_wake_up)
    ws.on('mycroft.stop', handle_stop)
    ws.on("mycroft.paired", handle_paired)

    event_thread = Thread(target=connect)
    event_thread.setDaemon(True)
    event_thread.start()

    try:
        loop.run()
    except KeyboardInterrupt, e:
        logger.exception(e)
        event_thread.exit()
        sys.exit()


if __name__ == "__main__":
    main()
