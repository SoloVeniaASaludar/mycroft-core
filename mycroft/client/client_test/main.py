#!/usr/bin/python
# -*- coding: iso-8859-15 -*-

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

import time
from threading import Thread

from mycroft.util.log import getLogger                      
from mycroft.configuration import ConfigurationManager      
from mycroft.messagebus.client.ws import WebsocketClient    
from mycroft.messagebus.message import Message              

ws = None
logger = getLogger("test_client")

ws = WebsocketClient()
def connect():
    ws.run_forever()
event_thread = Thread(target=connect)
event_thread.setDaemon(True)
event_thread.start()
time.sleep(3)

session = 1234
context = {'session': session}
lang='es'

payload = {
    'utterances': ['televisión pon canal seis'],
    'lang': lang,
}
msg=Message("recognizer_loop:utterance", payload, context)
ws.emit(msg)

