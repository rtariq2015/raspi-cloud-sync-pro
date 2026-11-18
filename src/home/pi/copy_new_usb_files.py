#!/usr/bin/env python

# Raspi Cloud Sync Pro modification: quarantine unsafe legacy runtime.
# Original source: Koseng/RaspiAsUSBStickWithCloudSync (GPL-3.0).
import os, subprocess, shutil, time, json
import logging, logging.handlers
from pathlib import Path
from inspect import getsourcefile

BASE_PATH = os.path.dirname(os.path.abspath(getsourcefile(lambda:0)))
DESTINATION_BASE_DIR = os.path.join(BASE_PATH, 'transfer')
COPYING_ACTIVE_FILE = os.path.join(DESTINATION_BASE_DIR, 'copyingActive')
USB_IMG = '/piusb.bin'
SOURCE_DIR = '/mnt/usb_share'


def setup_logging(log_file_full_path):
    base_path = os.path.dirname(log_file_full_path)
    if base_path: os.makedirs(base_path, exist_ok=True)
    log_formatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(log_file_full_path, maxBytes=500000, backupCount=7)
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)


def get_files_since_point_in_time(start_dir, point_in_time, newest_dir_count):
    all_files = []
    directories = []
    for item in os.scandir(start_dir):
        if item.is_file() and (max(item.stat().st_mtime, item.stat().st_ctime) > point_in_time):
            all_files.append(item.path)
        elif item.is_dir():
            directories.append(item)
    # Continue for the newest_dir_count newest folders in current directory
    directories.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for i in range(newest_dir_count):
        if len(directories) > i:
            all_files.extend(get_files_since_point_in_time(directories[i], point_in_time, newest_dir_count))
    return all_files


def get_all_file_entries_delete_empty_dirs(start_dir):
    all_file_entries = []
    items = os.scandir(start_dir)
    counter = 0
    for item in items:
        counter = counter + 1
        if item.is_file():
            all_file_entries.append(item)
        elif item.is_dir():
            all_file_entries.extend(get_all_file_entries_delete_empty_dirs(item.path))
    # Historical function name retained; empty directories are never deleted.
    return all_file_entries


def copy_file_with_directory_structure(source_base_path, source_file_path, dest_base_path):
    relative_path = os.path.relpath(source_file_path, source_base_path)
    destination_path =  os.path.join(dest_base_path, os.path.dirname(relative_path))
    logging.info(f"Copy {source_file_path} to {destination_path}")
    os.makedirs(destination_path, exist_ok=True)
    shutil.copy2(source_file_path, destination_path)


def get_loop_device():
    jsonLoop = json.loads(subprocess.run(['losetup', '-l', '-J'], capture_output=True, text=True).stdout)
    for ld in jsonLoop['loopdevices']:
        if ld['back-file'] == USB_IMG: 
            return ld['name']
    result = subprocess.run(['sudo', 'losetup', '-fP', '--show', USB_IMG ], capture_output=True, text=True)
    return result.stdout.strip()


def mount(device, mount_dir):
    raise RuntimeError(
        "Legacy USB image mounting is disabled: exclusive storage ownership is required"
    )


def umount(mount_dir):
    subprocess.call(['sudo', 'umount', '-f', mount_dir]) # -f force 
    time.sleep(0.5)


def check_and_delete_on_usb(cycle_counter, device, config):
    """Compatibility entry point: automatic USB deletion is permanently disabled."""
    logging.warning("Automatic USB deletion is disabled; all source files are retained")


def copy_new_files(last_modify_time, device):   
    current_modify_time = os.stat(USB_IMG).st_mtime
    if (current_modify_time > last_modify_time[0]):
        mount(device, SOURCE_DIR)
        destination_dir = os.path.join(DESTINATION_BASE_DIR, str(int(current_modify_time)))
        new_files = get_files_since_point_in_time(SOURCE_DIR, last_modify_time[0], newest_dir_count=2)
        Path(COPYING_ACTIVE_FILE).touch()
        for file in new_files:
            copy_file_with_directory_structure(SOURCE_DIR, file, destination_dir)
        if os.path.isfile(COPYING_ACTIVE_FILE):
            os.remove(COPYING_ACTIVE_FILE)
        umount(SOURCE_DIR)
        last_modify_time[0] = os.stat(USB_IMG).st_mtime
    

# ----------------
# MAIN
# ----------------
# This constant cannot be overridden by configuration or an environment variable.
# Re-enabling requires a reviewed code change and the storage safety work in docs/SAFETY.md.
LEGACY_RUNTIME_DISABLED = True


def main():
    if LEGACY_RUNTIME_DISABLED:
        logging.error("Legacy USB copying is disabled; see docs/SAFETY.md")
        return 2
    setup_logging(os.path.join(BASE_PATH, 'logs/copyLogging.log'))
    config = []
    cycle_counter = [0]
    last_modify_time = [os.stat(USB_IMG).st_mtime]

    try:
        logging.info("----STARTED copy_new_usb_files.py----")

        # load config file
        with open(os.path.join(BASE_PATH,'config.json')) as config_file:
            config = json.load(config_file)
        logging.info(f"Configuration loaded.")

        # get loop device for partition access
        loop_device = get_loop_device() + 'p1' # primary partition 1
        logging.info(f"Loop Device: {loop_device}")

        # main loop
        while (True):
            check_and_delete_on_usb(cycle_counter, loop_device, config)
            copy_new_files(last_modify_time, loop_device)
            time.sleep(config['CopyCheckCycleTime'] )

    except Exception as ex:
        logging.exception(ex)



if __name__ == '__main__':
    raise SystemExit(main())
