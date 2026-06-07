#
# KodeBlox Copyright 2019 Sayak Mukhopadhyay
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http: //www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import functools
import logging
import threading
import tkinter as tk
from os.path import dirname, join

import semantic_version
import sys
import time

import l10n
import myNotebook as nb
from config import config, appname, appversion
import compat
from discord_sdk.pypresence import Presence

plugin_name = "DiscordPresence"

logger = logging.getLogger(f'{appname}.{plugin_name}')

_ = functools.partial(l10n.Translations.translate, context=__file__)

CLIENT_ID = 386149818227097610

VERSION = '3.2.0'

# Add global var for Planet name (landing + around)
planet = ''
landingPad = '2'

this = sys.modules[__name__]  # For holding module globals


def update_presence():
    """Update the Discord Rich Presence with current activity."""
    if config.get_int("disable_presence") == 0:
        try:
            this.RPC.update(
                state=this.presence_state,
                details=this.presence_details,
                start=int(this.time_start)
            )
            logger.info("Successfully updated presence")
        except Exception as e:
            logger.error(f"Error updating presence: {e}")
    else:
        try:
            this.RPC.clear()
            logger.info("Presence cleared")
        except Exception as e:
            logger.error(f"Error clearing presence: {e}")


def plugin_prefs(parent, cmdr, is_beta):
    """
    Return a TK Frame for adding to the EDMC settings dialog.
    """
    this.disablePresence = tk.IntVar(value=config.get_int("disable_presence"))

    frame = nb.Frame(parent)
    nb.Checkbutton(frame, text="Disable Presence", variable=this.disablePresence).grid()
    nb.Label(frame, text='Version %s' % VERSION).grid(padx=10, pady=10, sticky=tk.W)

    return frame


def prefs_changed(cmdr, is_beta):
    """
    Save settings.
    """
    config.set('disable_presence', this.disablePresence.get())
    update_presence()


def plugin_start3(plugin_dir):
    """Initialize the Discord Rich Presence connection."""
    this.plugin_dir = plugin_dir
    this.discord_thread = threading.Thread(target=initialize_discord, args=(plugin_dir,), daemon=True)
    this.discord_thread.start()
    return 'DiscordPresence'


def plugin_stop():
    """Clean up Discord Rich Presence connection on plugin stop."""
    try:
        if this.RPC:
            this.RPC.clear()
            this.RPC.close()
            logger.info("Discord RPC closed")
    except Exception as e:
        logger.error(f"Error closing RPC: {e}")


def journal_entry(cmdr, is_beta, system, station, entry, state):
    """Process journal entry and update presence accordingly."""
    global planet
    global landingPad
    
    presence_state = this.presence_state
    presence_details = this.presence_details
    
    if entry['event'] == 'StartUp':
        presence_state = _('In system {system}').format(system=system)
        if station is None:
            presence_details = _('Flying in normal space')
        else:
            presence_details = _('Docked at {station}').format(station=station)
    elif entry['event'] == 'Location':
        presence_state = _('In system {system}').format(system=system)
        if station is None:
            presence_details = _('Flying in normal space')
        else:
            presence_details = _('Docked at {station}').format(station=station)
    elif entry['event'] == 'StartJump':
        presence_state = _('Jumping')
        if entry['JumpType'] == 'Hyperspace':
            presence_details = _('Jumping to system {system}').format(system=entry['StarSystem'])
        elif entry['JumpType'] == 'Supercruise':
            presence_details = _('Preparing for supercruise')
    elif entry['event'] == 'SupercruiseEntry':
        presence_state = _('In system {system}').format(system=system)
        presence_details = _('Supercruising')
    elif entry['event'] == 'SupercruiseExit':
        presence_state = _('In system {system}').format(system=system)
        presence_details = _('Flying in normal space')
    elif entry['event'] == 'FSDJump':
        presence_state = _('In system {system}').format(system=system)
        presence_details = _('Supercruising')
    elif entry['event'] == 'Docked':
        presence_state = _('In system {system}').format(system=system)
        presence_details = _('Docked at {station}').format(station=station)
    elif entry['event'] == 'Undocked':
        presence_state = _('In system {system}').format(system=system)
        presence_details = _('Flying in normal space')
    elif entry['event'] == 'ShutDown':
        presence_state = _('Connecting CMDR Interface')
        presence_details = ''
    elif entry['event'] == 'DockingGranted':
        landingPad = entry['LandingPad']
    elif entry['event'] == 'Music':
        if entry['MusicTrack'] == 'MainMenu':
            presence_state = _('Connecting CMDR Interface')
            presence_details = ''
    # Todo: This elif might not be executed on undocked. Functionality can be improved
    elif entry['event'] == 'Undocked' or entry['event'] == 'DockingCancelled' or entry['event'] == 'DockingTimeout':
        presence_details = _('Flying near {station}').format(station=entry['StationName'])
    # Planetary events
    elif entry['event'] == 'ApproachBody':
        presence_details = _('Approaching {body}').format(body=entry['Body'])
        planet = entry['Body']
    elif entry['event'] == 'Touchdown' and entry['PlayerControlled']:
        presence_details = _('Landed on {body}').format(body=planet)
    elif entry['event'] == 'Liftoff' and entry['PlayerControlled']:
        if entry['PlayerControlled']:
            presence_details = _('Flying around {body}').format(body=planet)
        else:
            presence_details = _('In SRV on {body}, ship in orbit').format(body=planet)
    elif entry['event'] == 'LeaveBody':
        presence_details = _('Supercruising')

    # EXTERNAL VEHICLE EVENTS
    elif entry['event'] == 'LaunchSRV':
        presence_details = _('In SRV on {body}').format(body=planet)
    elif entry['event'] == 'DockSRV':
        presence_details = _('Landed on {body}').format(body=planet)

    if presence_state != this.presence_state or presence_details != this.presence_details:
        this.presence_state = presence_state
        this.presence_details = presence_details
        update_presence()


def initialize_discord(plugin_dir):
    """Initialize Discord RPC connection with retry logic."""
    retry = True
    retry_count = 0
    max_retries = 30  # Retry for up to 3 seconds (30 * 0.1)
    
    while retry and retry_count < max_retries:
        time.sleep(0.1)
        try:
            this.RPC = Presence(CLIENT_ID)
            this.RPC.connect()
            logger.info("Discord RPC connected successfully")
            retry = False
        except Exception as e:
            retry_count += 1
            logger.debug(f"Attempting to connect to Discord RPC (attempt {retry_count}/{max_retries}): {e}")
    
    if retry_count >= max_retries:
        logger.error("Failed to connect to Discord RPC after maximum retries")
        this.RPC = None
        return
    
    this.presence_state = _('Connecting CMDR Interface')
    this.presence_details = ''
    this.time_start = time.time()
    this.disablePresence = None
    
    update_presence()
