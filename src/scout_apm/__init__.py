from __future__ import absolute_import


# pip needs to be imported before anything else (in particular the requests
# library), since it vendors stuff oddly.
#
# This is an unsupported use of pip, and we need to figure out a smoother way
# to detect loaded libraries. Check metadata.py for our usage.
import pip # noqa

# Python Modules
import logging
from os import getpid

from scout_apm.context import AgentContext
from scout_apm.core_agent_manager import CoreAgentManager
from scout_apm.metadata import AppMetadata

logger = logging.getLogger(__name__)


def install():
    logger.debug('APM Launching on PID: %s', getpid())
    CoreAgentManager().launch()
    AppMetadata.report()
    AgentContext.socket().stop()
