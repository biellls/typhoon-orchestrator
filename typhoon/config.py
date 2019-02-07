import os
from configparser import ConfigParser

from typhoon.settings import typhoon_directory


def settings_path():
    return os.path.join(typhoon_directory(), 'typhoonconfig.cfg')


def read_config():
    config = ConfigParser()
    config.read(settings_path())
    return config


def get(env, var):
    config = read_config()
    return config[env][var]