#!/usr/env/python

from __future__ import division, print_function

## Import General Tools
import sys
import os
import argparse
import logging
import re
import datetime
import glob
import subprocess
import paramiko
import shutil


def check_remote_shasum(remote_computer, remote_file, logger):

    if re.search('\$TGTNAME\-\$INTERVAL', remote_file):
        remote_file = remote_file.replace('$', '\$')
        logger.debug('  Replacing $ characters with \$ in {}'.format(remote_file))
    if re.search('\+', remote_file):
        remote_file = remote_file.replace('+', '\+')
        logger.debug('  Replacing + characters with \+ in {}'.format(remote_file))


    ansi_escape = re.compile(r'\x1b[^m]*m')
    shasum_cmd = 'shasum {}'.format(remote_file)
    stdin, stdout, stderr = remote_computer.exec_command(shasum_cmd)
    ## stdout
    try:
        lines_with_ansi = stdout.readlines()
        lines = []
        for line in lines_with_ansi:
            cleanedline = ansi_escape.sub('', line).strip('\n')
            lines.append(str(cleanedline))
            logger.debug('  STDOUT: {}'.format(cleanedline))
        stdout = lines
    except:
        stdout = []
    ## stderr
    try:
        lines_with_ansi = stderr.readlines()
        lines = []
        for line in lines_with_ansi:
            cleanedline = ansi_escape.sub('', line).strip('\n')
            lines.append(str(cleanedline))
            logger.debug('  STDERR: {}'.format(cleanedline))
        stderr = lines
    except:
        stderr = []

    if (len(stderr) == 0):
        matchsum = re.match('.*\s+{}'.format(remote_file), stdout[0])
        if matchsum:
            logger.debug('  SHASUM recieved')
            remote_shasum = stdout[0].split()[0]
        else:
            remote_shasum = None
    elif re.search('No such file or directory', stderr[0]):
        logger.info('  Did not find file on remote machine.')
        remote_shasum = None
    else:
        logger.warning('  STDERR: {}'.format(stderr))
        remote_shasum = None

    return remote_shasum


def copy_file(file, local_shasum, remote_file, remote_computer_string, remote_computer, logger, listFO):
    ## Create Directories on Remote Machine as Needed
    remote_dest2 = os.path.split(remote_file)[0]
    remote_dest1 = os.path.split(remote_dest2)[0]
    mkdir_cmd1 = 'mkdir {}'.format(remote_dest1)
    mkdir_cmd2 = 'mkdir {}'.format(remote_dest2)
    logger.debug('  Ensuring {} exists'.format(remote_dest1))
    stdin, stdout, stderr = remote_computer.exec_command(mkdir_cmd1)
    logger.debug('  Ensuring {} exists'.format(remote_dest2))
    stdin, stdout, stderr = remote_computer.exec_command(mkdir_cmd2)

    if re.search('\$TGTNAME\-\$INTERVAL', remote_file):
        remote_file = remote_file.replace('$', '\$')
        logger.debug('  Replacing $ characters with \$ in {}'.format(remote_file))

    ## Copy File
    scp_cmd = ['scp', file, '{}:{}'.format(remote_computer_string, remote_file)]
    logger.debug('  Running: {}'.format(' '.join(scp_cmd)))
    try:
        subprocess.call(scp_cmd)
    except:
        logger.warning('  scp command failed')
    remote_shasum = check_remote_shasum(remote_computer, remote_file, logger)
    if not remote_shasum:
        logger.warning('  Copy to remote machine failed')
        listFO.write('Failed: {},{},{}:{},{}\n'.format(file, local_shasum, '', '', ''))
        return False
    elif remote_shasum == local_shasum:
        logger.info('  SHASUMs match.')
        listFO.write('Success: {},{},{}:{},{}\n'.format(file, local_shasum, remote_computer_string, remote_file, remote_shasum))
        return True
    else:
        logger.warning('  SHASUMs do not match!')
        logger.debug('  local:  {}'.format(local_shasum))
        logger.debug('  remote: {}'.format(remote_shasum))
        listFO.write('Failed: {},{},{}:{},{}\n'.format(file, local_shasum, '', '', ''))
        return False



##-------------------------------------------------------------------------
## Copy Night
##-------------------------------------------------------------------------
def copy_night(telescope, date, verbose=False, skip_file_checksums=False, copy=True):

    ##-------------------------------------------------------------------------
    ## Create logger object
    ##-------------------------------------------------------------------------
    logger = logging.getLogger('ConfirmRemoteData_{}_{}'.format(telescope, date))
    logger.setLevel(logging.DEBUG)
    ## Set up console output
    LogConsoleHandler = logging.StreamHandler()
    if verbose:
        LogConsoleHandler.setLevel(logging.DEBUG)
    else:
        LogConsoleHandler.setLevel(logging.INFO)
    LogFormat = logging.Formatter('%(asctime)23s %(levelname)8s: %(message)s')
    LogConsoleHandler.setFormatter(LogFormat)
    logger.addHandler(LogConsoleHandler)
    ## Set up file output
    LogFileName = 'ConfirmRemoteData_{}_{}'.format(telescope, date)
    LogFilePath = os.path.join('/', 'Users', 'vysosuser', 'logs')
    LogFileHandler = logging.FileHandler(os.path.join(LogFilePath, LogFileName))
    LogFileHandler.setLevel(logging.DEBUG)
    LogFileHandler.setFormatter(LogFormat)
    logger.addHandler(LogFileHandler)

    logger.info('Checking data for {} for night of {}'.format(telescope, date))

    ##-------------------------------------------------------------------------
    ## Confirm Data on Drobo Matches Data in Hilo
    ##-------------------------------------------------------------------------
    drobo_path = os.path.join('/', 'Volumes', 'Drobo', telescope)
    extdrive_paths = [os.path.join('/', 'Volumes', 'WD500B', telescope),\
                      os.path.join('/', 'Volumes', 'WD500_C', telescope)]
    if os.path.exists(extdrive_paths[0]):
        extdrive_path = extdrive_paths[0]
    elif os.path.exists(extdrive_paths[1]):
        extdrive_path = extdrive_paths[1]
    else:
        logger.warning("Can't find path for external drive")
        extdrive_path = None

    ## Make list of files to analyze
    files = glob.glob(os.path.join(drobo_path, 'Images', date, '*.*'))
    if os.path.exists(os.path.join(drobo_path, 'Images', date, 'Calibration')):
        files.extend(glob.glob(os.path.join(drobo_path, 'Images', date, 'Calibration', '*.fts')))
    if os.path.exists(os.path.join(drobo_path, 'Images', date, 'AutoFlat')):
        files.extend(glob.glob(os.path.join(drobo_path, 'Images', date, 'AutoFlat', '*.fts')))
    files.extend(glob.glob(os.path.join(drobo_path, 'Logs', date, '*.*')))
    n_source_files = len(files)
    logger.info('Found {} files to analyze'.format(n_source_files))

    remote_computer_string = 'vysosuser@trapezium.ifa.hawaii.edu'
    remote_computer = paramiko.SSHClient()
    remote_computer.load_system_host_keys()
    remote_computer.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    remote_computer.connect(hostname=remote_computer_string.split('@')[1],\
                            username=remote_computer_string.split('@')[0])

    if telescope == 'V5':
        remote_path = os.path.join('/', 'Volumes', 'DroboPro1', 'VYSOS5_Data')
    elif telescope == 'V20':
        remote_path = os.path.join('/', 'Volumes', 'DroboPro1', 'VYSOS20_Data')
    else:
        logger.critical('Telescope is not set to V5 or V20')
        sys.exit(1)

    if not skip_file_checksums:
        ## Open Local File on Drobo with Results
        list_file = os.path.join(drobo_path, 'transfer_logs', 'remote_{}_{}.txt'.format(telescope, date))
        with open(list_file, 'w') as listFO:
            counter = 0
            for file in files:
                counter += 1
                logger.info('Checking file {}/{}: {}'.format(counter, len(files), file))
                filename = os.path.split(file)[1]
                local_shasum = subprocess.check_output(['shasum', file]).split()[0]
                remote_file = file.replace(drobo_path, remote_path)
                remote_shasum = check_remote_shasum(remote_computer, remote_file, logger)

                ## Check Remote SHASUM against local SHASUM
                if not remote_shasum:
                    if copy:
                        logger.info('  Copying file to remote machine')
                        copy_file(file, local_shasum, remote_file, remote_computer_string, remote_computer, logger, listFO)
                    else:
                        listFO.write('Failed: {},{},{}:{},{}\n'.format(file, local_shasum, '', '', ''))
                elif remote_shasum == local_shasum:
                    logger.info('  SHASUMs match.')
                    listFO.write('Success: {},{},{}:{},{}\n'.format(file, local_shasum, remote_computer_string, remote_file, remote_shasum))
                else:
                    logger.warning('  SHASUMs do not match!')
                    logger.debug('  local:  {}'.format(local_shasum))
                    logger.debug('  remote: {}'.format(remote_shasum))
                    if copy:
                        logger.info('  Copying file to remote machine')
                        copy_file(file, local_shasum, remote_file, remote_computer_string, remote_computer, logger, listFO)
                    else:
                        listFO.write('Failed: {},{},{}:{},{}\n'.format(file, local_shasum, '', '', ''))


    ##-------------------------------------------------------------------------
    ## Final Confirmation of Night
    ##-------------------------------------------------------------------------
    ## Count files in source directory, compare to count in remote directory
    ## Check that number of "success" lines in log is equal to number of files
    ## Check that number of "failure" lines in log is zero
    ## If all ok, then change name of data directory on USB drive to indicate
    ## that it can be deleted
    
    email_file = os.path.join(os.path.expanduser('~'), 'Dropbox', 'DataSyncReports', 
                              '{}_{}.txt'.format(telescope, date))

    outputlines = []
    
    def info(msg):
        logger.info(msg)
        outputlines.append('INFO: {}\n'.format(msg))

    def warning(msg):
        logger.warning(msg)
        outputlines.append('WARNING: {}\n'.format(msg))
    
    info('Starting final check of files for night')
    Fail = False
    info('  Found {} files on local drive'.format(n_source_files))
    
    ## Get count of files in remote directory
    ansi_escape = re.compile(r'\x1b[^m]*m')

    ls_cmd = 'ls -1 {}'.format(os.path.join(remote_path, 'Images', date, '*.*'))
    ls_cmd += ' ; ls -1 {}'.format(os.path.join(remote_path, 'Images', date, 'Calibration'))
    ls_cmd += ' ; ls -1 {}'.format(os.path.join(remote_path, 'Images', date, 'AutoFlat'))
    ls_cmd += ' ; ls -1 {}'.format(os.path.join(remote_path, 'Logs', date))

    logger.debug('  Counting files on remote machine using "{}"'.format(ls_cmd))
    stdin, stdout, stderr = remote_computer.exec_command(ls_cmd)
    ## stdout
    try:
        lines_with_ansi = stdout.readlines()
        lines = []
        for line in lines_with_ansi:
            cleanedline = ansi_escape.sub('', line).strip('\n')
            lines.append(str(cleanedline))
            logger.debug('  STDOUT: {}'.format(cleanedline))
        stdout = lines
    except:
        stdout = []

    n_remote_files = len(stdout)
    info('  Found {} files on remote drive'.format(n_remote_files))

    if n_source_files == n_remote_files:
        info('  Number of source and destination files match.  PASS')
    else:
        warning('  Number of source and destination files do NOT match.  FAIL')
        Fail = True

    ## Check for number of "success" lines in log
    info('Checking for reported success in transfer log')
    list_file = os.path.join(drobo_path, 'transfer_logs', 'remote_{}_{}.txt'.format(telescope, date))
    with open(list_file, 'r') as listFO:
        lines = listFO.readlines()
        n_loglines = len(lines)
        if n_source_files == n_loglines:
            info('  Number of source files and log lines match.  PASS')
        else:
            warning('  Number of source files and log lines do NOT match.  FAIL')
            Fail = True
        for line in lines:
            if not re.match('Success:.*', line):
                warning('Failure in Log: {}'.format(line))
                Fail = True

    ## Rename folders that are ok to delete on USB drive
    if extdrive_path and not Fail:
        if os.path.exists(os.path.join(extdrive_path, 'Images', date)):
            info('Renaming Images/{0} on USB drive to Images/ok2delete_{0}'.format(date))
            shutil.move(os.path.join(extdrive_path, 'Images', date),\
                        os.path.join(extdrive_path, 'Images', 'ok2delete_'+date))
        else:
            info('No Images/{0} found.  Already deleted?'.format(date))
        if os.path.exists(os.path.join(extdrive_path, 'Logs', date)):
            info('Renaming Logs/{0} on USB drive to Logs/ok2delete_{0}'.format(date))
            shutil.move(os.path.join(extdrive_path, 'Logs', date),\
                        os.path.join(extdrive_path, 'Logs', 'ok2delete_'+date))
        else:
            info('No Logs/{0} found.  Already deleted?'.format(date))

    if Fail:
        with open(email_file, 'w') as FO:
            for line in outputlines:
                FO.write('{}\n'.format(line))


def copy_nights(telescope, verbose=False, skip_file_checksums=False, copy=True):
    path = os.path.join('/', 'Volumes', 'WD500B', telescope, 'Images')
    dirs = glob.glob(os.path.join(path, '2*UT'))
    for dir in dirs:
        date = os.path.split(dir)[1]
        if re.match('\d{8}UT', date):
            copy_night(telescope, dir,
                       verbose=verbose,
                       skip_file_checksums=skip_file_checksums,
                       copy=copy)


def main():
    ##-------------------------------------------------------------------------
    ## Parse Command Line Arguments
    ##-------------------------------------------------------------------------
    ## create a parser object for understanding command-line arguments
    parser = argparse.ArgumentParser(
             description="Program description.")
    ## add flags
    parser.add_argument("-v", "--verbose",
        action="store_true", dest="verbose",
        default=False, help="Be verbose! (default = False)")
    parser.add_argument("--copy",
        action="store_true", dest="copy",
        default=True, help="Copy the file over if SHASUM mismatch?")
    parser.add_argument("--skip-file-checksums",
        action="store_true", dest="skip_file_checksums",
        default=False, help="Only do the check for the night, not file by file.")
    ## add arguments
    parser.add_argument("-t", "--telescope",
        dest="telescope", required=True, type=str,
        choices=["V5", "V20"],
        help="Telescope which took the data ('V5' or 'V20')")
    parser.add_argument("-d", "--date",
        type=str, dest="date",
        help="The date to copy.")
    args = parser.parse_args()

    if args.date:
        if re.match('\d{8}UT', args.date):
            date = args.date
        elif args.date == 'today':
            today = datetime.datetime.utcnow()
            date = today.strftime('%Y%m%dUT')
        elif args.date == 'yesterday':
            today = datetime.datetime.utcnow()
            oneday = datetime.timedelta(1, 0)
            date = (today - oneday).strftime('%Y%m%dUT')
        copy_night(args.telescope, date,\
                   verbose=args.verbose,\
                   skip_file_checksums=args.skip_file_checksums,\
                   copy=args.copy)
    else:
        copy_nights(args.telescope,
                    verbose=args.verbose,\
                    skip_file_checksums=args.skip_file_checksums,\
                    copy=args.copy)


if __name__ == '__main__':
    main()

