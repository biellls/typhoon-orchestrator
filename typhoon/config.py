import os
from configparser import ConfigParser

from typhoon.settings import typhoon_directory


def settings_path():
    return os.path.join(typhoon_directory(), 'typhoonconfig.cfg')


def read_config():
    config = ConfigParser()
    config.read(settings_path())
    return config


def get(env, var, default=None, mandatory=False):
    config = read_config()
    if mandatory and var not in config['env'].keys():
        raise ValueError(f'No attribute {var} in {env} config')
    return config[env].get(var, default)
