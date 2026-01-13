import logging
from colorama import Fore, Style


class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.INFO: Fore.LIGHTGREEN_EX,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED
    }


    def format(self, record):
        if record.levelno in self.COLORS:
            record.levelname = (f"{self.COLORS[record.levelno]}"
                                f"{record.levelname}{Style.RESET_ALL}")

        return super().format(record)


console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter(
    "%(levelname)s: %(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))

file_handler = logging.FileHandler("logs.txt")
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s"
))

logger = logging.getLogger("skynet")
logger.setLevel(logging.INFO)
#logger.addHandler(console_handler)
logger.addHandler(file_handler)

 
