import threading

from adapt.intent import IntentBuilder

from mycroft.skills.core import MycroftSkill
from mycroft.util.log import getLogger
from mycroft.messagebus.message import Message

logger = getLogger(__name__)
__author__ = 'seanfitz'

class SkillSession(object):
    def __init__(self, id, skill):
        self.id = id
        self.skill = skill
        self.msg_event = threading.Event()

    #
    # async to sync
    #

    def wait( self ):
        self.msg_event.clear()
        self.msg_event.wait()

    #
    # core methods
    #
        
    def record( self, rec_chars={} ):
        self.skill.emitter.emit(
             Message("record", rec_chars, 
                 context={ "session": self.id } ) )

    def speak(self, utterance, expect_response=False, context={} ):
        data = {'utterance': utterance,
                'expect_response': expect_response}
        self.skill.emitter.emit(Message("speak", data, context = context ))

    def speak_dialog(self, key, data={}, expect_response=False, context={} ):
        self.speak(
            self.skill.dialog_renderer.render(key, data),
            expect_response = expect_response,
            context = context )



class MultiThreadSkill(MycroftSkill):
    def __init__(self, name, session_class):
        super(MultiThreadSkill,self).__init__(name)
        self.session_class = session_class
        self.sessions = {}
        if not self.config:
            self.config={}
        

    def initialize(self):
	self.on('recognizer_loop:audio_output_end')
	self.on('recognizer_loop:record_end')

    # Override 
    def register_intent( self, intent, f ):
        def _wrapper(msg):
            session_id = msg.context.get("session")
            session = self.session_class( session_id, self )
            self.sessions[session_id] = session
            thread = threading.Thread( target = f, args = [ session, intent ] )
            thread.start()

        super(MultiThreadSkill,self).register_intent( intent, _wrapper)

    def on( self, event ):
        def _execute(msg):
            try:
                session_id = msg.context.get("session")
                self.session = self.sessions.get(session_id)
                if self.session:
                    self.session.msg_event.set()
            except:
                logger.error("error handling %s", event, exc_info=True)
                throw

	self.emitter.on(event, _execute)
