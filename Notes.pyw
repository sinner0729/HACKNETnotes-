import sys
import os
from pathlib import Path
from PySide6.QtWidgets import *
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QPainter, QColor
import threading

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    print("注意: pystray 或 PIL 未安装，托盘功能不可用")


def get_resource_path(relative_path):
    """获取资源文件的绝对路径，兼容开发和打包后的环境"""
    try:
        # PyInstaller 创建的临时文件夹中的路径
        base_path = getattr(sys, '_MEIPASS', None)
        if base_path:
            return os.path.join(base_path, relative_path)
    except Exception:
        pass
    
    # 开发环境中的路径
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


class NoteWidget(QWidget):
    deleteRequested = Signal(QWidget)
    contentChanged = Signal(str, str)

    def __init__(self, file_path, is_new=False):
        super().__init__()
        self.file_path = file_path
        self.is_new = is_new
        self.setup_ui()
        if not self.is_new:
            self.load_content()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.text_edit = QTextEdit()
        self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.text_edit.setMinimumHeight(36)
        self.text_edit.setMaximumHeight(2000)
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_edit.setLineWrapMode(QTextEdit.WidgetWidth)

        font = QFont("Consolas", 10)
        self.text_edit.setFont(font)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #001a00;
                color: #00ff00;
                border: 1px solid #004400;
                border-radius: 0px;
                padding: 6px;
                font-family: 'Consolas', monospace;
                font-size: 12px;
                selection-background-color: #006600;
                selection-color: #ffffff;
            }
        """)
        self.text_edit.textChanged.connect(self.on_text_changed)

        self.delete_btn = QPushButton("x", self.text_edit)
        self.delete_btn.setGeometry(self.text_edit.width() - 22, 2, 18, 18)
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #ff5555;
                border: none;
                font-weight: bold;
                font-size: 12px;
                padding: 0px;
            }
            QPushButton:hover { color: #ffaaaa; }
        """)
        self.delete_btn.clicked.connect(lambda: self.deleteRequested.emit(self))
        self.delete_btn.raise_()
        self.delete_btn.setFocusPolicy(Qt.NoFocus)

        layout.addWidget(self.text_edit)

        if self.is_new:
            note_id = self.file_path.stem
            self.text_edit.setPlaceholderText(f"> NOTE_{note_id}")

    def load_content(self):
        try:
            if self.file_path.exists():
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.text_edit.setPlainText(content)
        except Exception as e:
            print(f"加载笔记 {self.file_path} 时出错: {e}")
        finally:
            QTimer.singleShot(10, self.adjust_height)

    def on_text_changed(self):
        content = self.text_edit.toPlainText()
        self.save_to_file(content)
        self.contentChanged.emit(str(self.file_path), content)
        self.adjust_height()

    def save_to_file(self, content):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"保存笔记到 {self.file_path} 时出错: {e}")

    def adjust_height(self):
        doc = self.text_edit.document()
        new_height = int(doc.size().height()) + 16
        self.text_edit.setFixedHeight(max(36, min(new_height, 2000)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.delete_btn.setGeometry(self.text_edit.width() - 22, 2, 18, 18)


class TrayHandler(QObject):
    """处理托盘相关的信号，确保在主线程中执行"""
    show_window_signal = Signal()
    quit_app_signal = Signal()
    
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.show_window_signal.connect(self.window.show_window)
        self.quit_app_signal.connect(self.window.quit_app)
    
    def show_window(self):
        """由pystray调用的方法，发出信号在主线程中执行"""
        self.show_window_signal.emit()
    
    def quit_app(self):
        """由pystray调用的方法，发出信号在主线程中执行"""
        self.quit_app_signal.emit()


class DraggableWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.dragging = False
        self.offset = None
        self.tray_icon = None
        self.tray_thread = None
        
        # 获取数据目录路径，兼容打包环境
        self.data_dir = self.get_data_dir()
        self.data_dir.mkdir(exist_ok=True, parents=True)
        
        self.tray_handler = TrayHandler(self)
        self.setup_ui()
        self.load_or_create_notes()
        
        # 如果有托盘支持，设置托盘
        if TRAY_AVAILABLE:
            # 延迟一点时间设置托盘，确保窗口已经完全初始化
            QTimer.singleShot(100, self.setup_tray)
            
        # 窗口显示完成后，确保焦点正确
        QTimer.singleShot(200, self.ensure_focus)

    def get_data_dir(self):
        """获取数据目录，兼容开发和打包环境"""
        # 在开发环境中，使用当前目录下的notes文件夹
        # 在打包环境中，使用用户数据目录
        try:
            # 如果是打包版本
            if getattr(sys, 'frozen', False):
                # 获取用户数据目录
                if os.name == 'nt':  # Windows
                    appdata = os.getenv('APPDATA')
                    data_dir = Path(appdata) / "HacknetNotes" / "notes"
                else:  # Linux/Mac
                    home = Path.home()
                    data_dir = home / ".config" / "HacknetNotes" / "notes"
            else:
                # 开发环境，使用当前目录下的notes文件夹
                data_dir = Path(__file__).parent / "notes"
        except Exception as e:
            print(f"获取数据目录时出错: {e}")
            # 如果出错，回退到当前目录
            data_dir = Path(__file__).parent / "notes"
            
        return data_dir

    def setup_tray(self):
        """设置系统托盘图标（生成带'HN'文字的图标）"""
        if self.tray_icon:
            # 如果托盘图标已经存在，不再重新创建
            return
            
        try:
            # 1. 创建一个带透明通道的图像 (RGBA模式)
            width, height = 64, 64
            image = Image.new('RGBA', (width, height), (0, 0, 0, 0)) # 完全透明背景
            draw = ImageDraw.Draw(image)
            
            # 2. 绘制一个深绿色圆角矩形作为背景
            background_color = (0, 40, 0, 220) # 深绿色，带透明度
            margin = 8
            draw.rounded_rectangle(
                [margin, margin, width - margin, height - margin],
                radius=10,
                fill=background_color
            )
            
            # 3. 在中心绘制绿色"HN"文字
            font = None
            try:
                # 尝试加载系统字体
                if os.name == 'nt':  # Windows
                    font_path = get_resource_path("fonts/consola.ttf")
                    if os.path.exists(font_path):
                        font = ImageFont.truetype(font_path, 28)
                    else:
                        # 尝试系统字体
                        font = ImageFont.truetype("Consolas", 28)
                else:  # Linux/Mac
                    font = ImageFont.truetype("DejaVuSans", 28)
            except:
                try:
                    font = ImageFont.truetype("Arial", 28)
                except:
                    font = ImageFont.load_default()
            
            text = "HN"
            # 获取文字大小并计算居中位置
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except:
                # 老版本PIL的兼容写法
                text_width, text_height = draw.textsize(text, font=font)
            
            text_x = (width - text_width) // 2
            text_y = (height - text_height) // 2
            
            draw.text((text_x, text_y), text, fill=(0, 255, 0, 255), font=font) # 亮绿色文字
            
            # 4. 定义托盘菜单 - 使用tray_handler中的方法
            menu = pystray.Menu(
                pystray.MenuItem('显示', self.tray_handler.show_window),
                pystray.MenuItem('退出', self.tray_handler.quit_app)
            )
            
            # 5. 创建托盘图标
            self.tray_icon = pystray.Icon("hacknet_notes", image, "Hacknet Notes", menu)
            
            # 6. 使用单独的线程运行托盘图标，避免阻塞主线程
            def run_tray():
                try:
                    self.tray_icon.run()
                except Exception as e:
                    print(f"托盘图标运行出错: {e}")
                    # 如果托盘运行失败，重置tray_icon为None
                    self.tray_icon = None
            
            self.tray_thread = threading.Thread(target=run_tray, daemon=True)
            self.tray_thread.start()
            
        except Exception as e:
            print(f"设置托盘图标时出错: {e}")
            self.tray_icon = None

    def hide_to_tray(self):
        """隐藏窗口到系统托盘"""
        if self.isHidden():
            return  # 如果已经隐藏，直接返回，避免重复操作
        
        # 立即隐藏窗口，不使用延迟
        self.hide()
        
        # 确保托盘图标存在
        if TRAY_AVAILABLE and not self.tray_icon:
            self.setup_tray()

    def show_window(self):
        """显示窗口（由TrayHandler调用）"""
        if not self.isHidden():
            return  # 如果已经显示，直接返回
        
        self.show()
        self.activateWindow()
        self.raise_()
        
        # 确保窗口获得焦点
        self.setFocus()
        self.ensure_focus()
    
    def ensure_focus(self):
        """确保窗口获得焦点"""
        self.activateWindow()
        self.raise_()
        if hasattr(self, 'notes_layout') and self.notes_layout.count() > 0:
            # 尝试让第一个笔记获得焦点
            first_note = self.notes_layout.itemAt(0).widget()
            if first_note and hasattr(first_note, 'text_edit'):
                first_note.text_edit.setFocus()

    def quit_app(self):
        """退出应用程序（由TrayHandler调用）"""
        # 停止托盘图标
        if hasattr(self, 'tray_icon') and self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception as e:
                print(f"停止托盘图标时出错: {e}")
        
        # 退出Qt应用
        QApplication.quit()

    def setup_ui(self):
        # 黄金分割比例窗口：380x615 (1:1.618)
        self.setWindowTitle("> HACKNET_NOTES")
        self.resize(380, 615)
    
        # 设置窗口标志
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea { background-color: transparent; border: none; }
            QScrollBar:vertical {
                background-color: rgba(0, 10, 0, 120);
                width: 8px;
                border: 1px solid #002200;
            }
            QScrollBar::handle:vertical {
                background-color: #004400;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background-color: #006600; }
        """)

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")

        self.notes_layout = QVBoxLayout(self.scroll_content)
        self.notes_layout.setAlignment(Qt.AlignTop)
        self.notes_layout.setSpacing(6)

        scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(scroll_area)

        # 底部按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(6)
        button_width = 184
        button_height = 28

        # 添加笔记按钮
        self.add_button = QPushButton("> ADD_NOTE")
        self.add_button.setFixedSize(button_width, button_height)
        self.add_button.setStyleSheet("""
            QPushButton {
                background-color: #002200;
                color: #00ff00;
                border: 1px solid #004400;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                padding: 4px;
            }
            QPushButton:hover {
                background-color: #003300;
                border: 1px solid #006600;
                color: #aaffaa;
            }
            QPushButton:pressed {
                background-color: #001100;
                border: 1px solid #004400;
                padding-top: 5px;
                padding-bottom: 3px;
            }
        """)
        self.add_button.clicked.connect(self.add_new_note)

        # 最小化按钮
        self.minimize_button = QPushButton("_ TO TRAY")
        self.minimize_button.setFixedSize(button_width, button_height)
        self.minimize_button.setStyleSheet("""
            QPushButton {
                background-color: #220000;
                color: #ff5555;
                border: 1px solid #440000;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                padding: 4px;
            }
            QPushButton:hover {
                background-color: #330000;
                border: 1px solid #660000;
                color: #ffaaaa;
            }
            QPushButton:pressed {
                background-color: #110000;
                border: 1px solid #440000;
                padding-top: 5px;
                padding-bottom: 3px;
            }
        """)
        self.minimize_button.clicked.connect(self.hide_to_tray)

        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.minimize_button)
        main_layout.addLayout(button_layout)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setBrush(QColor(0, 8, 0, 220))
        painter.setPen(QColor(0, 34, 0))
        painter.drawRect(self.rect())
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QColor(0, 68, 0))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

    def load_or_create_notes(self):
        try:
            note_files = sorted(self.data_dir.glob("*.txt"), key=lambda x: int(x.stem))
            for file in note_files:
                self.create_note_widget(file, is_new=False)
            if len(note_files) == 0:
                self.add_new_note()
        except Exception as e:
            print(f"加载笔记时出错: {e}")
            # 出错时至少创建一个新笔记
            self.add_new_note()

    def add_new_note(self):
        try:
            next_id = self._get_next_available_id()
            new_file_path = self.data_dir / f"{next_id}.txt"
            new_file_path.touch()
            self.create_note_widget(new_file_path, is_new=True)
        except Exception as e:
            print(f"添加新笔记时出错: {e}")
        
    def _get_next_available_id(self):
        """获取下一个可用的笔记ID"""
        existing_ids = []
        try:
            for file in self.data_dir.glob("*.txt"):
                try:
                    existing_ids.append(int(file.stem))
                except ValueError:
                    pass
        except Exception as e:
            print(f"扫描现有笔记时出错: {e}")
        
        if not existing_ids:
            return 1
        
        # 查找最小的未使用ID
        existing_ids.sort()
        for i in range(1, len(existing_ids) + 2):
            if i not in existing_ids:
                return i
        return len(existing_ids) + 1

    def create_note_widget(self, file_path, is_new=False):
        note = NoteWidget(file_path, is_new)
        note.deleteRequested.connect(self.remove_note)
        note.contentChanged.connect(self.on_note_content_changed)
        self.notes_layout.addWidget(note)
        if is_new:
            QTimer.singleShot(50, lambda: note.text_edit.setFocus())
        QTimer.singleShot(10, note.adjust_height)

    def on_note_content_changed(self, file_path_str, content):
        pass

    def remove_note(self, note_widget):
        try:
            if note_widget.file_path.exists():
                note_widget.file_path.unlink()
        except Exception as e:
            print(f"删除文件 {note_widget.file_path} 时出错: {e}")
        self.notes_layout.removeWidget(note_widget)
        note_widget.deleteLater()

    def mousePressEvent(self, event):
        """鼠标在窗口边缘按下时启动拖拽"""
        if event.button() == Qt.LeftButton:
            margin = 8  # 边缘拖拽区域宽度
            rect = self.rect()
            inner_rect = rect.adjusted(margin, margin, -margin, -margin)

            # 如果点击位置在边缘区域内
            if not inner_rect.contains(event.pos()):
                self.dragging = True
                self.offset = event.globalPosition().toPoint() - self.pos()
                self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        """拖拽时移动窗口"""
        if self.dragging and self.offset:
            self.move(event.globalPosition().toPoint() - self.offset)

    def mouseReleaseEvent(self, event):
        """释放鼠标时结束拖拽"""
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self.offset = None
            self.setCursor(Qt.ArrowCursor)

    def closeEvent(self, event):
        """重写关闭事件：隐藏到托盘而不是退出"""
        if TRAY_AVAILABLE:
            event.ignore()  # 忽略关闭事件
            self.hide_to_tray()  # 隐藏窗口
        else:
            event.accept()


if __name__ == "__main__":
    # 设置异常处理
    def exception_handler(exc_type, exc_value, exc_traceback):
        import traceback
        print("未处理的异常:", exc_type.__name__)
        print("错误信息:", exc_value)
        print("跟踪信息:")
        traceback.print_tb(exc_traceback)
    
    sys.excepthook = exception_handler
    
    # 创建应用
    app = QApplication(sys.argv)
    app.setFont(QFont("Consolas", 9))
    
    # 设置应用程序信息
    app.setApplicationName("Hacknet Notes")
    app.setOrganizationName("Hacknet")
    
    # 创建窗口
    window = DraggableWindow()
    window.show()
    
    # 运行应用
    sys.exit(app.exec())