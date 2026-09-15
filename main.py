import sys
import os
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt
from app.main_window import MainWindow


def show_alpha_warning():
    msg = QMessageBox()
    msg.setWindowTitle("CamerUdioStt — Alpha Release")
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setStyleSheet("""
        QMessageBox {
            background-color: #1a1a2e;
            color: #e0e0e0;
        }
        QMessageBox QLabel {
            color: #e0e0e0;
            font-size: 12px;
        }
        QMessageBox QPushButton {
            background-color: #2a2a4a;
            color: #00d4ff;
            border: 1px solid #00d4ff;
            border-radius: 4px;
            padding: 6px 16px;
            font-size: 12px;
            min-width: 80px;
        }
        QMessageBox QPushButton:hover {
            background-color: #00d4ff;
            color: #1a1a2e;
        }
    """)

    message = """
    <div style="font-family: 'Segoe UI', Arial, sans-serif;">
    <p style="color: #00d4ff; font-size: 14px; font-weight: bold; margin-bottom: 12px;">
    ⚠ ВНИМАНИЕ: ALPHA-ВЕРСИЯ ПРОГРАММНОГО ОБЕСПЕЧЕНИЯ
    </p>

    <p style="color: #c0c0c0; line-height: 1.6; margin-bottom: 10px;">
    Данное программное обеспечение находится в стадии <b style="color: #ff6b6b;">активной разработки (Alpha V)</b> 
    и предназначено исключительно для тестирования и ознакомительных целей.
    </p>

    <p style="color: #c0c0c0; line-height: 1.6; margin-bottom: 10px;">
    <b style="color: #ffd93d;">Отказ от ответственности:</b><br>
    Автор программного обеспечения <u>не несёт ответственности</u> за любые возможные сбои, 
    нестабильную работу системы / данного ПО или иные негативные последствия, 
    возникшие в результате использования данного ПО.
    </p>

    <p style="color: #c0c0c0; line-height: 1.6; margin-bottom: 10px;">
    <b style="color: #6bff6b;">Рекомендации при возникновении сбоев:</b><br>
    • При невозможности закрыть приложение штатным способом, используйте диспетчер задач<br>
    • Комбинация клавиш: <code style="background: #2a2a4a; padding: 2px 6px; border-radius: 3px; color: #00d4ff;">Ctrl + Alt + Delete</code><br>
    • В критических случаях выполните перезагрузку системы
    </p>

    <p style="color: #888; font-size: 11px; margin-top: 15px; border-top: 1px solid #333; padding-top: 10px;">
    Нажимая "Принимаю", вы подтверждаете, что ознакомлены с условиями и принимаете все риски.
    </p>
    </div>
    """

    msg.setText(message)
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg.button(QMessageBox.StandardButton.Ok).setText("Принимаю")
    msg.exec()


def check_dependencies():
    """Проверяет наличие VB-CABLE и OBS Studio"""
    issues = []

    # Проверка VB-CABLE
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        cable_found = any("CABLE" in d["name"].upper() or "VB-AUDIO" in d["name"].upper() 
                         for d in devices)
        if not cable_found:
            issues.append({
                "name": "VB-CABLE",
                "desc": "Виртуальный аудиокабель для вывода обработанного звука",
                "link": "https://vb-audio.com/Cable/"
            })
    except Exception:
        issues.append({
            "name": "VB-CABLE",
            "desc": "Виртуальный аудиокабель для вывода обработанного звука",
            "link": "https://vb-audio.com/Cable/"
        })

    # Проверка OBS Studio
    obs_paths = [
        r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
        r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\obs-studio\bin\64bit\obs64.exe"),
    ]
    obs_found = any(os.path.exists(p) for p in obs_paths)

    # Также проверим через реестр
    if not obs_found:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                r"SOFTWARE\OBS Studio")
            winreg.CloseKey(key)
            obs_found = True
        except:
            pass

    if not obs_found:
        issues.append({
            "name": "OBS Studio",
            "desc": "Виртуальная камера для вывода обработанного видео",
            "link": "https://obsproject.com/"
        })

    # Если есть проблемы — показать окно
    if issues:
        msg = QMessageBox()
        msg.setWindowTitle("CamerUdioStt — Проверка зависимостей")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setStyleSheet("""
            QMessageBox {
                background-color: #1a1a2e;
                color: #e0e0e0;
            }
            QMessageBox QLabel {
                color: #e0e0e0;
                font-size: 12px;
            }
            QMessageBox QPushButton {
                background-color: #2a2a4a;
                color: #00d4ff;
                border: 1px solid #00d4ff;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 12px;
                min-width: 80px;
            }
            QMessageBox QPushButton:hover {
                background-color: #00d4ff;
                color: #1a1a2e;
            }
        """)

        items_html = ""
        for issue in issues:
            items_html += f"""
            <div style="background: #2a2a4a; border-radius: 6px; padding: 10px; margin: 8px 0; border-left: 3px solid #ff6b6b;">
                <p style="color: #ff6b6b; font-weight: bold; margin: 0 0 6px 0;">✗ {issue['name']} не обнаружен</p>
                <p style="color: #c0c0c0; margin: 0 0 8px 0; font-size: 11px;">{issue['desc']}</p>
                <p style="margin: 0;">
                    <a href="{issue['link']}" style="color: #00d4ff; text-decoration: none;">
                    📥 Скачать {issue['name']} →
                    </a>
                </p>
            </div>
            """

        message = f"""
        <div style="font-family: 'Segoe UI', Arial, sans-serif;">
        <p style="color: #00d4ff; font-size: 14px; font-weight: bold; margin-bottom: 12px;">
        🔍 ПРОВЕРКА ЗАВИСИМОСТЕЙ
        </p>

        <p style="color: #c0c0c0; line-height: 1.6; margin-bottom: 12px;">
        Для полноценной работы приложения необходимы следующие компоненты:
        </p>

        {items_html}

        <p style="color: #888; font-size: 11px; margin-top: 15px; border-top: 1px solid #333; padding-top: 10px;">
        После установки компонентов перезапустите приложение.
        Некоторые компоненты могут потребовать перезагрузки системы.
        </p>
        </div>
        """

        msg.setText(message)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.button(QMessageBox.StandardButton.Ok).setText("Понятно")
        msg.exec()


def main():
    print("Приложение запущено", flush=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    show_alpha_warning()
    check_dependencies()

    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    print("Окно показано", flush=True)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
