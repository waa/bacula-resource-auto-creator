#!/usr/bin/python3
# ----------------------------------------------------------------------------
# - bacula-resource-auto-creator.py
# ----------------------------------------------------------------------------

# waa - 20240131 - Initial re-write of my `checkDriveIndexes.sh` script from
#                  bash into Python.
#                - The final goal will be to use this initial process of
#                  identifying libraries, and drives, and then tying the
#                  drives to Bacula SD `DriveIndex` settings as a base to
#                  generate cut-n-paste Bacula resource configurations for
#                  the Director Storage, the SD Autochanger it points to
#                  and the Drive Devices in the Autochanger.
# waa - 20280815 - This script now accomplishes the goals stated above.
#                  More features to be added and fixes as issues are found.
#
# The latest version of this script may be found at: https://github.com/waa
#
# ----------------------------------------------------------------------------
#
# BSD 2-Clause License
#
# Copyright (c) 2024-2026, William A. Arlofski waa@revpol.com
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# 1.  Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#
# 2.  Redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
# IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
# TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
# TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# ----------------------------------------------------------------------------
#
# ==================================================
# Nothing below this line should need to be modified
# ==================================================

# Import the required modules
# ---------------------------
import os
import re
import sys
import socket
import argparse
import subprocess
from time import sleep
from pathlib import Path
from random import randint
from datetime import datetime
from ipaddress import ip_address, IPv4Address

# Set some variables
# ------------------
progname = 'Bacula Resource Auto Creator'
version = '0.26'
reldate = 'August 16, 2026'
progauthor = 'Bill Arlofski'
authoremail = 'waa@revpol.com'
scriptname = 'bacula-resource-auto-creator.py'
prog_info_txt = progname + ' - v' + version + ' - ' + scriptname \
              + '\nBy: ' + progauthor + ' ' + authoremail + ' (c) ' + reldate + '\n'

# List of tape libraries to skip during testing
# ---------------------------------------------
# If testing with mhVTL, skip the scsi-SSTK_L700_XYZZY_A library because it has LTO6/8
# tapes and LTO6/8 drives. An error is thrown if an LTOx tape is loaded into LTOy drive
# -------------------------------------------------------------------------------------
libs_to_skip = ['scsi-SSTK_L700_XYZZY_A', 'scsi-SSTK_L700_XYZZY_A-changer', 'otherLibToSkip']

# Define the argparse arguments, descriptions, defaults, etc
# waa - Something to look into: https://www.reddit.com/r/Python/comments/11hqsbv/i_am_sick_of_writing_argparse_boilerplate_code_so/
# ---------------------------------------------------------------------------------------------------------------------------------
parser = argparse.ArgumentParser(prog=scriptname, description='Automatically create Bacula Storage, Tape Library Autochanger, and Tape Device Resource(s).')
parser.add_argument('-v', '--version',    help='Print the script version.', version=scriptname + " v" + version, action='version')
parser.add_argument('-a', '--address',    help='The FQDN (preferred), hostname, or IP address the Director will use to connect to this SD.', default=None)
parser.add_argument('-b', '--bweb',       help='Do we create Director Storage resource configuration files for each SD Drive?', action='store_true')
parser.add_argument('-d', '--debug',      help='Enables logging of additional information such as the full "mt", "mtx", "ls", and "lsscsi" outputs.', action='store_true')
parser.add_argument('-m', '--mcj',        help='The MaximumConcurrentJobs per SD Drive Device.', type=int, default=1)
parser.add_argument('-o', '--offline',    help='Do the drives require to be sent the "offline" command before unload?', action='store_true')
parser.add_argument('-p', '--password',   help='The password the Director will use to connect to the SD.', default=None)
parser.add_argument('-s', '--sleep_secs', help='The number of seconds to sleep between mtx and mt commands while this script is running.', type=int, default=15)
args = parser.parse_args()

# Now for some functions
# ----------------------
def now():
    'Return the current date/time in human readable format.'
    return datetime.today().strftime('%Y%m%d%H%M%S')

def usage():
    'Show the instructions and script information.'
    parser.print_help()
    print('\n' + prog_info_txt)
    sys.exit(1)

def log(text):
    'Given some text, print it to stdout and write it to the log file.'
    print(text)
    with open(log_file, 'a+') as file:
        file.write(text + '\n')

def log_cmd_results(result):
    'Given a subprocess.run() result object, clean up the extra line feeds from stdout and stderr and log them.'
    stdout = result.stdout.rstrip('\n')
    stderr = result.stderr.rstrip('\n')
    if stdout == '':
        stdout = 'N/A'
    if stderr == '':
        stderr = 'N/A'
    log(' - returncode: ' + str(result.returncode))
    log(' - stdout: ' + ('\n[begin stdout]\n' + stdout + '\n[end stdout]' if '\n' in stdout else stdout))
    log(' - stderr: ' + ('\n[begin stderr]\n' + stderr + '\n[end stderr]' if '\n' in stderr else stderr))

def chk_cmd_result(result, cmd):
    'Given a result object, check the returncode, then log and exit if non zero.'
    if result.returncode != 0:
        if 'Device or resource busy' in result.stderr:
            log('  - Device is "busy", probably locked by another process. Please be sure \'bacula-sd\' is not running')
            log('   - Exiting with errorlevel ' + str(result.returncode))
        else:
            log('\n- Command: ' + cmd)
            log_cmd_results(result)
            log('- Exiting with returncode ' + str(result.returncode))
        log('\n' + '='*(prog_info_txt.find('\n')) + '\n' + prog_info_txt)
        sys.exit(result.returncode)

def get_shell_result(cmd):
    'Given a command to run, return the subprocess.run() result.'
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

def get_uname():
    'Get the OS uname to be use in other tests.'
    log('- Getting the OS uname for use in other tests')
    cmd = 'uname'
    result = get_shell_result(cmd)
    if debug:
        log(' - shell command: ' + cmd)
        log_cmd_results(result)
    chk_cmd_result(result, cmd)
    return result.stdout.rstrip('\n')

def get_ready_str():
    'Determine the OS to set the correct mt "ready" string.'
    log('- Determining the correct mt "ready" string')
    if uname == 'Linux':
        if os.path.isfile('/etc/debian_version'):
            cmd = 'mt --version | grep "mt-st"'
            result = get_shell_result(cmd)
            if debug:
                log('  - mt command: ' + cmd)
                log_cmd_results(result)
            if result.returncode == 1:
                return 'drive status'
        else:
            cmd = 'mt --version | grep "GNU cpio"'
            result = get_shell_result(cmd)
            if debug:
                log(' - mt command: ' + cmd)
                log_cmd_results(result)
            if result.returncode == 0:
                return 'drive status'
        return 'ONLINE'
    elif uname == 'SunOS':
        return 'No Additional Sense'
    elif uname == 'FreeBSD':
        return 'Current Driver State: at rest.'
    elif uname == 'OpenBSD':
        return 'ds=3<Mounted>'
    else:
        log('- The \'uname\' command failed to identify the OS or this OS is not supported.\n - OS reported: \'' + uname + '\'\n')
        usage()

def lib_or_drv_status(cmd):
    result = get_shell_result(cmd)
    chk_cmd_result(result, cmd)
    if debug:
        log('- Command: ' + cmd)
        log_cmd_results(result)
    return result

def loaded(lib_status, index):
    'If the drive (index) is loaded, return the slot and volume that is in it, otherwise return 0, 0'
    drive_loaded_line = re.search('Data Transfer Element ' + str(index) + ':Full.*', lib_status)
    if drive_loaded_line is not None:
        slot_and_vol_loaded = (re.sub(r'^Data Transfer Element.*Element (\d+) Loaded.*= (\w+)', '\\1 \\2', drive_loaded_line.group(0))).split()
        slot_loaded = slot_and_vol_loaded[0]
        vol_loaded = slot_and_vol_loaded[1]
        log(' - Drive ' \
            + str(index) + ' is loaded with volume ' + vol_loaded + ' from slot ' + slot_loaded)
        if debug:
            log('  - loaded output: ' + slot_loaded)
        return slot_loaded, vol_loaded
    else:
        log(' - Drive ' + str(index) + ' is empty')
        return '0', '0'

def get_random_slot(lib_status):
    'Return a pseudo-random slot that contains a tape and the volume name in the slot.'
    full_slots_lst = re.findall(r'Storage Element \d+:Full(?!.*CLN).*', lib_status)
    if len(full_slots_lst) == 0:
        return 0, 0
    else:
        rand_int = randint(0, len(full_slots_lst) - 1)
        slot = re.sub('Storage Element ([0-9].?):Full.*', '\\1', full_slots_lst[rand_int])
        vol = re.sub('.*:VolumeTag=(.*).*', '\\1', full_slots_lst[rand_int]).rstrip()
        return slot, vol

def unload(lib, slot, drive):
    cmd = 'mtx -f ' + byid_node_dir_str + '/' + lib + ' unload ' + slot + ' ' + str(drive)
    result = get_shell_result(cmd)
    if debug:
        log('  - mtx command: ' + cmd)
        log_cmd_results(result)
    if result.returncode == 0:
        log('    - Unload successful')
    else:
        log('    - Unload failed')
    chk_cmd_result(result, cmd)
    return

def write_res_file(filename, text):
    'Given a filename and some text, write the text to the file.'
    with open(filename, 'a+') as file:
        file.write(text)

def is_ip_address(address):
    'Given a string, determine if it is a valid IP address'
    try:
        ip_address(address)
        return True
    except ValueError:
        return False

def resolve(address):
    'Given a string, determine if it is resolvable to an IP address'
    try:
        data = socket.gethostbyname_ex(address)
        return data[2][0]
    except Exception:
        return False

def get_ip_address(address):
    'Given an address string, check if it is an IP, if not try to resolve it, and return an IP address'
    if is_ip_address(address):
        if debug:
            log(' - \'' + address + '\' is an IP address')
        return address
    else:
        if debug:
            log(' - \'' + address + '\' is not an IP address. Attempting to resolve...')
        ip = resolve(address)
        if ip == False:
            if debug:
                log('  - Oops, cannot resolve FQDN/host \'' + address + '\'')
            return False
        else:
            if debug:
                log('  - FQDN/host \'' + address + '\' resolves to IP address: ' + ip)
            return address

def get_sd_addr():
    'Request the FQDN, Hostname, or IP address, then verify it is an IP address or is resolvable.'
    i = input('\n- Please enter the FQDN (preferred), Hostname, or IP address that the Director & Clients will use to contact this SD: ').strip()
    addr = get_ip_address(i)
    if not addr:
        print('  - The Hostname or FQDN \'' + i + '\' does not resolve to an IP address')
        print('  - Please try again')
        return False
    else:
        return addr

def get_sd_pass():
    'Request the password that the Director will use to contact the Storage Daemon.'
    sd_pass = input('\n- Please enter the password the Director will use to contact this SD: ').strip()
    if len(sd_pass) == 0:
        print(' - Password cannot be an empty string')
        return False
    else:
       sd_pass_ok = input(' - Is the password \'' + sd_pass + '\' OK to use? [Y/n]: ').strip() or 'Y'
       if sd_pass_ok not in ('Y', 'y'):
           return False
       else:
           return sd_pass

def match_sym_to_scsi(sym):
    'Given a /sys/class/(scsi_generic|scsi_tape)/(sg*|nst*), return H:C:T:L'
    dev_path = sym / 'device'
    full = os.path.realpath(dev_path)
    name = os.path.basename(full)
    if re.fullmatch(r'\d+:\d+:\d+:\d+', name):
        return name
    return None

def get_sys_class(sg_or_tape):
    'Get /sys/class/(scsi_generic|(scsi_tape/nst*|lin_tape/IBM*)) nodes'
    if sg_or_tape == 'sg':
        sym_type = 'scsi_generic'
    elif sg_or_tape == 'tape':
        sym_type = tape_type
    dir = Path('/sys/class/' + sym_type)
    sym_links = sorted(dir.iterdir())
    return sym_links

# ================
# BEGIN THE SCRIPT
# ================
# Set the log and output directories. This directory will also
# be where we write the cut-n-paste Bacula resource configurations
# ----------------------------------------------------------------
date_stamp = now()
lower_name_and_time = progname.replace(' ', '-').lower() + '_' + date_stamp
work_dir = '/tmp/' + lower_name_and_time
dir_st_dir = work_dir + '/Director/Storage'
sd_auto_dir = work_dir + '/Storage/Autochanger'
sd_device_dir = work_dir + '/Storage/Device'
log_file = work_dir + '/' + lower_name_and_time + '.log'

# Create the work_dir directory and resource subdirectories
# ---------------------------------------------------------
os.makedirs(dir_st_dir)
os.makedirs(sd_auto_dir)
os.makedirs(sd_device_dir)

# Assign variables from argparse Namespace
# ----------------------------------------
sd_addr = args.address
bweb = args.bweb
debug = args.debug
drive_mcj = args.mcj
offline = args.offline
sd_pass = args.password
sleep_secs = args.sleep_secs

# Create the lib_dict dictionary. It will hold {'libraryName': (index, 'drive_byid_node', 'st#', 'sg#'),...}
# ----------------------------------------------------------------------------------------------------------
lib_dict = {}

# Create the string added to Resource config files 'Description =' line
# ---------------------------------------------------------------------
created_by_str = 'Created by ' + progname + ' v' + version + ' - ' + date_stamp

# Set up the text string templates for the three types of
# resource configuration files that need to be created
# -------------------------------------------------------
director_storage_tpl = """Storage {
  Name =
  Description =
  Autochanger =
  Device =
  MediaType =
  Address =
  Password =
  SdPort = "9103"
  MaximumConcurrentJobs =
}
"""

storage_autochanger_tpl = """Autochanger {
  Name =
  Description =
  ChangerDevice =
  ChangerCommand = "/opt/bacula/scripts/mtx-changer %c %o %S %a %d"
  # waa - 20260814 - My drop-in mtx-changer script replacement. See https://github.com/waa for details
  # --------------------------------------------------------------------------------------------------
  # ChangerCommand = "/opt/bacula/scripts/mtx-changer-python.py -s autochanger_name %c %o %S %a %d -i %i -j %j"
  Device =
}
"""

storage_device_tpl = """Device {
  Name =
  Description =
  DriveIndex =
  DeviceType = "Tape"
  MediaType =
  Autochanger = "yes"
  AlwaysOpen = "yes"
  AutomaticMount = "yes"
  LabelMedia = "no"
  RandomAccess = "no"
  RemovableMedia = "yes"
  ArchiveDevice =
  ControlDevice =
  MaximumConcurrentJobs =
  AlertCommand = "/opt/bacula/scripts/tapealert %l"
  # waa - 20260814 - My drop-in tapealert script replacement. See https://github.com/waa for details
  # ------------------------------------------------------------------------------------------------
  # AlertCommand = "/opt/bacula/scripts/bacula-tapealert.py %a -i %i -e admin@example.com --logging --file /opt/bacula/log/storage_device_nst_node"
  # Tested best with btape on LTO8 and LTO9 drives
  # ----------------------------------------------
  MaximumFileSize = 32GB
  MaximumBlockSize = 2097152
}
"""

# Log the startup header
# ----------------------
hdr = '[ Starting ' + progname + ' v' + version + ' ]'
log('\n\n' + '='*10 + hdr + '='*10)
log('- Command line: ' + str(' '.join(sys.argv)))
log('- Work directory: ' + work_dir)
log('- Logging to file: ' + lower_name_and_time + '.log')

# Log the setting of the 'debug' variable
# ---------------------------------------
log('- The \'debug\' variable is ' + str(debug) + ', additional information will ' \
     + ('' if debug else 'not ') + 'be logged')

# Log the setting of the 'bweb' variable
# --------------------------------------
log('- The \'bweb\' variable is ' + str(bweb) + '. Will ' + ('' if bweb else 'not ') \
     + 'create individual Director Storage resource configuration files for each drive')

# Log the setting of the 'offline' variable
# -----------------------------------------
log('- The \'offline\' variable is ' + str(offline) + '. Will ' + ('' if offline else 'not ') \
     + 'send each drive the \'offline\' command before attempting to unload')

# Using argparse, with a default of '1' set for the drive_mcj,
# so just log the variable and where it came from
# ------------------------------------------------------------
log('- Each Drive Device will have \'MaximumConcurrentJobs = "' + str(drive_mcj) + '"\' ' \
     + ('(from command line)' if any(x in sys.argv for x in ('-m', '--mcj')) else '(default)'))

# Using argparse, with a default of '15' set for the sleep_secs
# variable, so just log the variable and where it came from
# -------------------------------------------------------------
log('- Sleep time between \'mtx\' and \'mt\' commands is ' + str(sleep_secs) + ' seconds ' \
     + ('(from command line)' if any(x in sys.argv for x in ('-s', '--sleep_secs')) else '(default)'))

# Ask for the Hostname, FQDN, or IP address needed to contact this SD
# -------------------------------------------------------------------
if sd_addr is None or not get_ip_address(sd_addr):
    sd_addr = False
    while not sd_addr:
        sd_addr = get_sd_addr()
    log(('   ' if debug else '') + '- Will use \'Address = "' + sd_addr + '"\' in the Director Storage resource to contact this SD')
else:
    log('- Will use \'Address = "' + sd_addr + '"\' (from command line) in the Director Storage resource to contact this SD')

# Ask for the password needed to contact this SD from the Director
# ----------------------------------------------------------------
if sd_pass is None or sd_pass == '':
    sd_pass = False
    while not sd_pass:
        sd_pass = get_sd_pass()
    log('  - Will use \'Password = "' + sd_pass + '"\' in the Director Storage resource to contact this SD')
else:
    log('- Will use \'Password = "' + sd_pass + '"\' (from command line) in the Director Storage resource to contact this SD')

# Get the OS's uname to be used in other tests
# --------------------------------------------
uname = get_uname()

# Check the OS to assign the 'ready' variable
# to know when a drive is loaded and ready.
# -------------------------------------------
ready = get_ready_str()

# Check for lin_tape driver
# -------------------------
log('- Checking for lin_tape driver')
cmd = 'lsmod | grep "^lin_tape" | wc -l'
result = get_shell_result(cmd)
chk_cmd_result(result, cmd)
if debug:
    log(' - lsmod command: ' + cmd)
    log_cmd_results(result)
if result.stdout.rstrip('\n') == '1':
    log('  - Found the lin_tape kernel driver loaded')
    dev_node = 'IBMtape'
    tape_type = 'lin_tape'
    byid_node_dir_str = '/dev/lin_tape/by-id'
else:
    log(' - Did not find the lin_tape kernel driver loaded')
    dev_node = 'nst'
    tape_type = 'scsi_tape'
    byid_node_dir_str = '/dev/tape/by-id'

# Create the byid_txt from all symlinks in /dev/tape/by-id directory
# ------------------------------------------------------------------
log('- Getting \'by-id\' device nodes')
cmd = 'ls -l ' + byid_node_dir_str + ' | grep "^lrw" | tr -s \' \' | cut -d \' \' -f9- | sort'
result = get_shell_result(cmd)
chk_cmd_result(result, cmd)
if debug:
    log(' - ls command: ' + cmd)
    log_cmd_results(result)
byid_txt = result.stdout.rstrip('\n')

# Get lsscsi output for use later to determine Library and tape drive sg# nodes
# -----------------------------------------------------------------------------
log('- Getting \'lsscsi\' output')
cmd = 'lsscsi -g | grep "tape\\|mediumx"'
result = get_shell_result(cmd)
chk_cmd_result(result, cmd)
if debug:
    log(' - lsscsi command: ' + cmd)
    log_cmd_results(result)
lsscsi_txt = result.stdout.rstrip('\n')

# Get the list of tape libraries' sg nodes
# ----------------------------------------
log('- Getting the list of tape libraries\' sg nodes')
libs_sg_lst = re.findall(r'.* mediumx .*/(sg\d+)', lsscsi_txt)
libs_sg_lst.sort()
num_libs = len(libs_sg_lst)
log(' - Found ' + str(num_libs) + ' librar' + ('ies' if num_libs == 0 or num_libs > 1 else 'y'))
log('  - Library sg node' + ('s' if num_libs > 1 else '') + ': ' + str(', '.join(libs_sg_lst)))

# Get the corresponding by-id node from the libraries' sg nodes
# -------------------------------------------------------------
if num_libs != 0:
    sg_scsi_lst = []
    tape_scsi_lst = []
    nst_node_tuple_lst =[]
    libs_byid_nodes_lst = []
    drive_byid_st_sg_lst = []
    log('- Determining libraries\' by-id nodes from their sg nodes')
    for lib_sg in libs_sg_lst:
        # This 'if' is to resolve Github issue #2. User's lsscsi output
        # showed two sg nodes for libraries, but the byid_txt output showed
        # only one, breaking the append() call below
        # waa - 20260814 - This so called fix from back in 2025 will not resolve
        #                  the issue. The problem is that this script currently
        #                  does not support multipathed tape libraries and drives
        #                - This fixes the issue where udev recently started creating
        #                  multiple /dev/tape/by-id symlinks per device.
        #                - This needs a proper attempt at a multipath fix :)
        # --------------------------------------------------------------------------
        if byid_txt.find(lib_sg):
            tmp_lst = (re.findall(r'(.+?) -> .*/' + lib_sg + '.*', byid_txt))
            # Get the shortest symlink name for consistency in 'ChangerDevice' names
            # ----------------------------------------------------------------------
            libs_byid_nodes_lst.append(min(tmp_lst, key=len))
        else:
            log(' - WARN: The libary sg node ' + lib_sg + ' does not appear in the \'byid_txt\', skipping')
    log(' - Library by-id node' + ('s' if num_libs > 1 else '') + ': ' + str(', '.join(libs_byid_nodes_lst)))
# Get each drive's by-id node, nst# node, and sg# node and create
# the drive_byid_st_sg_lst [('drive_byid_node', 'st#', 'sg#'),...]
# ----------------------------------------------------------------
log('- Generating the tape drive list [(\'drive_byid_node\', \'st node\', \'sg node\'),...]')

# Build the list of tuples of [('sg_symlink', 'H:C:T:L'),...]
# -----------------------------------------------------------
sg_sym_links = get_sys_class('sg')
for sym in sg_sym_links:
    result = match_sym_to_scsi(sym)
    sg_scsi_lst.append((str(sym), result))

# Build the list of tuples of [('tape_symlink', 'H:C:T:L'),...]
# -------------------------------------------------------------
tape_sym_links = get_sys_class('tape')
for sym in tape_sym_links:
    result = match_sym_to_scsi(sym)
    tape_scsi_lst.append((str(sym), result))

# Create the nst_node_tuple_lst consisting of tuples of ('shortest -nst symlink', '/dev/real_node')
# -------------------------------------------------------------------------------------------------
byid_nodes_lst = re.findall(r'(.+?-nst) -> .*/(' + dev_node + '\d+)', byid_txt)
for node in byid_nodes_lst:
    tmp_lst = re.findall(r'(.+?-nst) -> .*/(' + node[1] + ')', byid_txt)
    if (not any(tmp_lst[0][1] in i for i in nst_node_tuple_lst)):
        shortest_nst = min(tmp_lst, key=lambda t: len(t[0]))
        nst_node_tuple_lst.append(shortest_nst)

# Iterate through all entries in nst_node_tuple_lst, appending the proper sg node
# to the tuples, creating a list of tuples like: [('nst', '/dev/real_node', 'sg')]
# --------------------------------------------------------------------------------
for tuple in nst_node_tuple_lst:
    # To reliably find the /dev/sg# node for a tape drive, we get the tape's
    # H:C:T:L scsi from tape_scsi_lst and match it to the H:C:T:L in
    # sg_scsi_lst and grab the sg_symlink from that tuple
    # This works for /dev/tape/by-id/*.nst, and /dev/lintape/IBMtape* devices
    # -----------------------------------------------------------------------
    tape_nst = tuple[1]
    for tape_nst_tuple in tape_scsi_lst:
        if tape_nst in tape_nst_tuple[0]:
            scsi = tape_nst_tuple[1]
            break
    for sg_scsi_tuple in sg_scsi_lst:
        if scsi in sg_scsi_tuple[1]:
            sg = re.sub('.*/(.*)', '\\1', sg_scsi_tuple[0])
            break
    drive_byid_st_sg_lst.append((tuple[0], tuple[1], sg))
    num_drives = len(drive_byid_st_sg_lst)

if debug:
    log('drive_byid_st_sg_lst:\n---------------------\n' + str(drive_byid_st_sg_lst))
log(' - Found ' + str(num_drives) + ' drive' + ('s' if num_drives > 1 else ''))
log('  - Drive sg node' + ('s' if num_drives > 1 else '') + ': ' + str(', '.join(r[2] for r in drive_byid_st_sg_lst)))
log('  - Drive by-id node' + ('s' if num_drives > 1 else '') + ': ' + str(', '.join([r[0] for r in drive_byid_st_sg_lst])))
log('- Startup complete')
# If 'offline' is True send the offline command to all drives first
# -----------------------------------------------------------------
hdr = '\nChecking If We Send The \'offline\' Command To All Drives In The Librar' + ('ies' if num_libs > 1 else 'y') + ' Found\n'
log('\n\n' + '='*(len(hdr) - 2) + hdr + '='*(len(hdr) - 2))
if offline:
    log('- The \'offline\' variable is True, sending all empty drives the \'mt offline\' command')
    for drive_byid in drive_byid_st_sg_lst:
        log(' - Checking drive ' + byid_node_dir_str + '/' + drive_byid[0])
        cmd = 'mt -f ' + byid_node_dir_str + '/' + drive_byid[0] + ' status'
        result = get_shell_result(cmd)
        if re.search(ready, result.stdout, re.DOTALL):
            print('  - Drive ' + byid_node_dir_str + '/' + drive_byid[0] + ' is loaded, sending \'mt offline\' command')
            cmd = 'mt -f ' + byid_node_dir_str + '/' + drive_byid[0] + ' offline'
            result = get_shell_result(cmd)
            chk_cmd_result(result, cmd)
            if debug:
                log('  - mt command: ' + cmd)
                log_cmd_results(result)
        else:
            print('  - Drive ' + byid_node_dir_str + '/' + drive_byid[0] + ' is empty')
else:
    log('- The \'offline\' variable is False, skip sending all drives the \'mt offline\' command')

# For each library found, unload each of the drives in it before
# starting the process of identifying the Bacula DriveIndexes
# --------------------------------------------------------------
hdr = '\nUnloading All (' + str(len(drive_byid_st_sg_lst)) + ') Tape Drives In The (' + str(num_libs) + ') Librar' + ('ies' if num_libs > 1 else 'y') + ' Found\n'
log('\n\n' + '='*(len(hdr) - 2) + hdr + '='*(len(hdr) - 2))
for lib in libs_byid_nodes_lst:
    lib_status = lib_or_drv_status('mtx -f ' + byid_node_dir_str + '/' + lib + ' status')
    num_drives = len(re.findall('Data Transfer Element', lib_status.stdout, flags = re.DOTALL))
    hdr = '\n' + lib + ': Unloading (' + str(num_drives) + ') Tape Drives\n'
    log('-'*(len(hdr) - 2) + hdr + '-'*(len(hdr) - 2))
    if lib in libs_to_skip:
        log(lib + ' is in the \'libs_to_skip\' list, skipping...\n')
        continue
    else:
        # Unload all the drives in the library
        # ------------------------------------
        drive_index = 0
        while drive_index < num_drives:
            log('- Checking if a tape is in drive ' + str(drive_index))
            slot_loaded, vol_loaded = loaded(lib_status.stdout, drive_index)
            if slot_loaded != '0':
                log('  - Unloading volume ' + vol_loaded + ' from drive ' + str(drive_index) + ' to slot ' + slot_loaded)
                unload(lib, slot_loaded, drive_index)
            drive_index += 1
        log('')

# Now, iterate through each Library found, get the number of drives
# in it, then load a tape into each one, and attempt to identify
# the by-id node having a tape in it, and correlate its drive index
# -----------------------------------------------------------------
hdr = '\nIterating Through Each Library Found\n'
log('\n' + '='*(len(hdr) - 2) + hdr + '='*(len(hdr) - 2))
for lib in libs_byid_nodes_lst:
    lib_status = lib_or_drv_status('mtx -f ' + byid_node_dir_str + '/' + lib + ' status')
    num_drives = len(re.findall('Data Transfer Element', lib_status.stdout, flags = re.DOTALL))
    hdr = '\nLibrary \'' + lib + '\' with (' + str(num_drives) + ') drives\n'
    log('-'*(len(hdr) - 2) + hdr + '-'*(len(hdr) - 2))
    if lib in libs_to_skip:
        log(lib + ' is in the \'libs_to_skip\' list, skipping...\n')
        continue
    else:
        drive_index = 0
        while drive_index < num_drives:
            hdr = '\nIdentifying DriveIndex ' + str(drive_index) + '\n'
            log('-'*(len(hdr) - 2) + hdr + '-'*(len(hdr) - 2))
            slot, vol  = get_random_slot(lib_status.stdout)
            if slot == 0 and vol == 0:
                log(' - No full (non cleaning tape) slots found in library...')
                break
            log('- Loading volume ' + vol + ' from slot ' + slot + ' into drive ' + str(drive_index))
            cmd = 'mtx -f ' + byid_node_dir_str + '/' + lib + ' load ' + slot + ' ' + str(drive_index)
            result = get_shell_result(cmd)
            if debug:
                log('  - mtx command: ' + cmd)
                log_cmd_results(result)
            if result.returncode == 0:
                log(' - Loaded OK')
            else:
                log(' - Load FAILED')
            chk_cmd_result(result, cmd)
            log('  - Sleeping ' + str(sleep_secs) + ' second' + ('s' if sleep_secs != 1 else '') + ' to allow drive to settle')
            sleep(sleep_secs)

            # Test by-id device nodes with mt to identify drive's Bacula 'DriveIndex' setting
            # -------------------------------------------------------------------------------
            for drive_byid_node in drive_byid_st_sg_lst:
                if debug:
                    log('- Checking drive by-id node \'' + byid_node_dir_str + '/' + drive_byid_node[0] + '\'')
                result = lib_or_drv_status('mt -f ' + byid_node_dir_str + '/' + drive_byid_node[0] + ' status')
                if re.search(ready, result.stdout, re.DOTALL):
                    log(' - ' + ready + ': Tape ' + vol + ' is loaded in ' + byid_node_dir_str + '/' + drive_byid_node[0])
                    log('  - This is Bacula \'DriveIndex = ' + str(drive_index) + '\'')
                    # We found the drive with the tape loaded in it so
                    # add the current lib, drive_index, drive by-id node,
                    # st# and sg# to the lib_dict dictionary, and remove
                    # the drive_by-id_node from the drive_byid_st_sg_lst
                    # list
                    # ---------------------------------------------------
                    if lib in lib_dict:
                        lib_dict[lib].append((drive_index, drive_byid_node[0], drive_byid_node[1], drive_byid_node[2]))
                    else:
                        lib_dict[lib] = [(drive_index, drive_byid_node[0], drive_byid_node[1], drive_byid_node[2])]
                    drive_byid_st_sg_lst.remove(drive_byid_node)
                    # Now unload the drive
                    # --------------------
                    log('   - Unloading drive ' + str(drive_index))
                    unload(lib, slot, drive_index)
                    break
                else:
                    if debug:
                        log(' - EMPTY: Drive by-id node \'' + drive_byid_node[0] + '\' is empty')
            drive_index += 1
        log('')
hdr = '[ Bacula Drive \'DriveIndex\' -> \'ArchiveDevice\' settings ]'
log('\n' + '='*8 + hdr + '='*8) 
for lib in lib_dict:
    hdr = '\nLibrary: ' + lib + '\n'
    log('-'*(len(hdr) - 2) + hdr + '-'*(len(hdr) - 2))
    for index_byid_st_sg_tuple in lib_dict[lib]:
        log('DriveIndex = ' + str(index_byid_st_sg_tuple[0]))
        log(' ControlDevice = ' + index_byid_st_sg_tuple[3])
        log(' ArchiveDevice = ' + byid_node_dir_str + '/' + index_byid_st_sg_tuple[1])
    log('')
if len(drive_byid_st_sg_lst) != 0:
    drive_byid_st_sg_lst.sort()
    hdr = '\nStand Alone Drive' + ('s' if len(drive_byid_st_sg_lst) > 1 else '') + ' (May be in a library that was skipped)\n'
    log('-'*(len(hdr) - 2) + hdr + '-'*(len(hdr) - 2))
    log(', '.join([byid for byid, st, sg in drive_byid_st_sg_lst]))
log('='*80)

# Generate the Bacula resource cut-n-paste configurations
# -------------------------------------------------------
hdr = '\nGenerating Bacula Resource Configuration Files For Each Library Found\n'
log('\n\n' + '='*(len(hdr) - 2) + hdr + '='*(len(hdr) - 2))
for lib in lib_dict:
    hdr = '\nLibrary: ' + lib + '\n'
    log('-'*(len(hdr) - 2) + hdr + '-'*(len(hdr) - 2))
    autochanger_name = 'Autochanger_' + lib.replace('scsi-', '')

    # Director Storage
    # ----------------
    res_txt = director_storage_tpl
    log('- Generating Director Storage Resource for Autochanger:')
    log(' - ' + autochanger_name)
    res_txt = res_txt.replace('Name =', 'Name = "' + autochanger_name + '"')
    res_txt = res_txt.replace('Description =', 'Description = "Autochanger with (' \
            + str(len(lib_dict[lib])) + ') drives - ' + created_by_str + '"')
    res_txt = res_txt.replace('Address =', 'Address = "' + sd_addr + '"')
    res_txt = res_txt.replace('Password =', 'Password = "' + sd_pass + '"')
    res_txt = res_txt.replace('Autochanger =', 'Autochanger = "' + autochanger_name + '"')
    res_txt = res_txt.replace('Device =', 'Device = "' + autochanger_name + '"')
    res_txt = res_txt.replace('MaximumConcurrentJobs =', 'MaximumConcurrentJobs = "' + str(len(lib_dict[lib]) * drive_mcj) + '"')
    res_txt = res_txt.replace('MediaType =', 'MediaType = "' + lib.replace('scsi-', '') + '"')
    write_res_file(dir_st_dir + '/' + autochanger_name + '.cfg', res_txt)

    if bweb:
        # Director Storage -> SD Device(s) - This is primarily for BWeb
        # -------------------------------------------------------------
        log(' - The \'bweb\' variable is True, generating Director Storage Resource configuration files for each drive')
        dev = 0
        while dev < len(lib_dict[lib]):
            # Create a Director Storage resource config file for each drive device in the Autochanger
            # ---------------------------------------------------------------------------------------
            drv_res_txt = director_storage_tpl
            log('  - Generating Director Storage Resource: ' + autochanger_name + '_Dev' + str(dev))
            drv_res_txt = drv_res_txt.replace('Name =', 'Name = "' + autochanger_name + '_Dev' + str(dev) + '"')
            drv_res_txt = drv_res_txt.replace('Description =', 'Description = "Stand-Alone Drive Device ' \
                        + str(dev) + ' - ' + created_by_str + '"')
            drv_res_txt = drv_res_txt.replace('Address =', 'Address = "' + sd_addr + '"')
            drv_res_txt = drv_res_txt.replace('Password =', 'Password = "' + sd_pass + '"')
            drv_res_txt = drv_res_txt.replace('Autochanger =', 'Autochanger = "' + autochanger_name + '"')
            drv_res_txt = drv_res_txt.replace('Device =', 'Device = "' + autochanger_name + '_Dev' + str(dev) + '"')
            drv_res_txt = drv_res_txt.replace('MaximumConcurrentJobs =', 'MaximumConcurrentJobs = "' + str(drive_mcj) + '"')
            drv_res_txt = drv_res_txt.replace('MediaType =', 'MediaType = "' + lib.replace('scsi-', '') + '"')
            write_res_file(dir_st_dir + '/' + autochanger_name + '_Dev' + str(dev) + '.cfg', drv_res_txt)
            dev += 1

    # Storage Autochanger
    # -------------------
    res_txt = storage_autochanger_tpl
    log('- Generating Storage Autochanger and Device Resources:')
    res_txt = res_txt.replace('Name =', 'Name = "' + autochanger_name + '"')
    res_txt = res_txt.replace('Description =', 'Description = "' + created_by_str + '"')
    res_txt = res_txt.replace('ChangerDevice =', 'ChangerDevice = "' + byid_node_dir_str + '/' + lib + '"')
    res_txt = res_txt.replace('autochanger_name', autochanger_name)
    log(' - ' + autochanger_name + ' with (' + str(len(lib_dict[lib])) + ') drives')
    dev = 0
    autochanger_dev_str = ''
    while dev < len(lib_dict[lib]):
        # Storage Device resource config file for each drive device in the Autochanger
        # ----------------------------------------------------------------------------
        drv_res_txt = storage_device_tpl
        log('  - ' + autochanger_name + '_Dev' + str(dev))
        autochanger_dev_str += '"' + autochanger_name + '_Dev' + str(dev) + '"' + (', ' if dev <= (len(lib_dict[lib]) - 2) else '')
        drv_res_txt = drv_res_txt.replace('Name =', 'Name = "' + autochanger_name + '_Dev' + str(dev) + '"')
        drv_res_txt = drv_res_txt.replace('Description =', 'Description = "Drive ' + str(dev) \
                    + ' in ' + autochanger_name + ' - ' + created_by_str + '"')
        drv_res_txt = drv_res_txt.replace('DriveIndex =', 'DriveIndex = "' + str(dev) + '"')
        drv_res_txt = drv_res_txt.replace('MediaType =', 'MediaType = "' + lib.replace('scsi-', '') + '"')
        drv_res_txt = drv_res_txt.replace('MaximumConcurrentJobs =', 'MaximumConcurrentJobs = "' + str(drive_mcj) + '"')
        for index_byid_st_sg_tuple in lib_dict[lib]:
            if index_byid_st_sg_tuple[0] == dev:
                archive_device = index_byid_st_sg_tuple[1]
                control_device = index_byid_st_sg_tuple[3]
                continue
        drv_res_txt = drv_res_txt.replace('ArchiveDevice =', 'ArchiveDevice = "' + byid_node_dir_str + '/' + archive_device + '"')
        drv_res_txt = drv_res_txt.replace('ControlDevice =', 'ControlDevice = "/dev/' + control_device + '"')
        drv_res_txt = drv_res_txt.replace('storage_device_nst_node', autochanger_name + '_Dev' + str(dev) + '.log')
        write_res_file(sd_device_dir + '/' + autochanger_name + '_Dev' + str(dev) + '.cfg', drv_res_txt)
        dev += 1
    res_txt = res_txt.replace(' Device =', ' Device = ' + autochanger_dev_str)
    write_res_file(sd_auto_dir + '/' + autochanger_name + '.cfg', res_txt)
    log('- Storage Autochanger and Device Resources Done\n')

# Print location of log file and resource config files
# ----------------------------------------------------
hdr = '\nDONE: Bacula resource configuration files and script log in: ' + work_dir + '\n'
log('\n' + '='*(len(hdr) - 2) + hdr + '='*(len(hdr) - 2))
log(prog_info_txt)
