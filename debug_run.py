import sys
import os
import traceback

log = open(os.path.join(os.path.dirname(__file__), 'debug.log'), 'w')

def log_write(msg):
    log.write(msg + '\n')
    log.flush()
    print(msg, flush=True)

try:
    log_write('1 - start')
    from PyQt6.QtWidgets import QApplication
    log_write('2 - PyQt imported')
    app = QApplication(sys.argv)
    log_write('3 - app created')
    from app.main_window import MainWindow
    log_write('4 - MainWindow imported')
    w = MainWindow()
    log_write('5 - MainWindow created')
    w.show()
    log_write('6 - shown')
    w.raise_()
    log_write('7 - raised')
    w.activateWindow()
    log_write('8 - activated')
    log_write('9 - starting event loop')
    app.exec()
    log_write('10 - done')
except Exception as e:
    import traceback
    log_write(f'ERROR: {e}')
    log_write(traceback.format_exc())
finally:
    log.close()
