import logging
import os
import datetime

class SingletonType(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        """
        Calls the singleton logger
        """
        if cls not in cls._instances:
            cls._instances[cls] = super(SingletonType, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class LoggerUtil(object, metaclass=SingletonType):
    """Simple singleton logger utility class
    """
    _logger = None

    def __init__(self):
        self._logger = logging.getLogger("robust_edge_testbed_log")
        self._logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s \t [%(levelname)s | %(filename)s:%(lineno)s] > %(message)s')

        now = datetime.datetime.now()
        dirname = "./log"

        if not os.path.isdir(dirname):
            os.mkdir(dirname)
        fileHandler = logging.FileHandler(dirname + "/log_" + now.strftime("%Y-%m-%d")+".log")

        streamHandler = logging.StreamHandler()

        fileHandler.setFormatter(formatter)
        streamHandler.setFormatter(formatter)

        self._logger.addHandler(fileHandler)
        self._logger.addHandler(streamHandler)

    def getLogger(self):
        """
        Gets the singleton logger
        """
        return self._logger
