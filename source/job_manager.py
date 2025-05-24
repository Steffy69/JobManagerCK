import sys
import os
import shutil
import json
import requests
import subprocess
from datetime import datetime
from PyQt5 import QtWidgets, QtCore, uic
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox, QFrame, QLabel, QVBoxLayout, QProgressDialog
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QMimeData, QTimer
from PyQt5.QtGui import QIcon, QPalette, QColor, QDragEnterEvent, QDropEvent

# Version of this application
CURRENT_VERSION = "1.0.0"

# URL to check for updates (you'll need to host this JSON file somewhere)
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/Steffy69/JobManagerCK/main/version.json"
# Example of version.json content:
# {
#   "version": "1.0.1",
#   "download_url": "https://your-server.com/jobmanager/JobManager.exe",
#   "release_notes": "Fixed bug with file transfers"
# }

class UpdateChecker(QThread):
    """Thread to check for updates without blocking the UI"""
    update_available = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def run(self):
        try:
            response = requests.get(UPDATE_CHECK_URL, timeout=5)
            if response.status_code == 200:
                update_info = response.json()
                if self.is_newer_version(update_info['version'], CURRENT_VERSION):
                    self.update_available.emit(update_info)
        except Exception as e:
            self.error.emit(str(e))
    
    def is_newer_version(self, remote_version, current_version):
        """Compare version strings (e.g., "1.0.1" > "1.0.0")"""
        remote_parts = [int(x) for x in remote_version.split('.')]
        current_parts = [int(x) for x in current_version.split('.')]
        
        for i in range(max(len(remote_parts), len(current_parts))):
            remote_part = remote_parts[i] if i < len(remote_parts) else 0
            current_part = current_parts[i] if i < len(current_parts) else 0
            if remote_part > current_part:
                return True
            elif remote_part < current_part:
                return False
        return False

class UpdateDownloader(QThread):
    """Thread to download updates"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, download_url):
        super().__init__()
        self.download_url = download_url
        
    def run(self):
        try:
            # Download to a temporary file
            temp_file = "JobManager_update.exe"
            
            response = requests.get(self.download_url, stream=True)
            total_size = int(response.headers.get('content-length', 0))
            
            downloaded = 0
            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = int((downloaded / total_size) * 100)
                            self.progress.emit(progress)
            
            self.finished.emit(True, temp_file)
        except Exception as e:
            self.finished.emit(False, str(e))

class DropZone(QFrame):
    """Custom widget for drag and drop functionality"""
    fileDropped = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameStyle(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #aaa;
                border-radius: 5px;
                background-color: #f0f0f0;
                min-height: 80px;
            }
            QFrame:hover {
                border-color: #777;
                background-color: #e8e8e8;
            }
        """)
        
        # Add label
        layout = QVBoxLayout()
        self.label = QLabel("Drag and drop a job folder here\n(from USB, Desktop, etc.)")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: #666;")
        layout.addWidget(self.label)
        self.setLayout(layout)
        
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QFrame {
                    border: 2px solid #4CAF50;
                    border-radius: 5px;
                    background-color: #e8f5e9;
                    min-height: 80px;
                }
            """)
            
    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #aaa;
                border-radius: 5px;
                background-color: #f0f0f0;
                min-height: 80px;
            }
            QFrame:hover {
                border-color: #777;
                background-color: #e8e8e8;
            }
        """)
        
    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files and os.path.isdir(files[0]):
            self.fileDropped.emit(files[0])
        self.dragLeaveEvent(event)

class FileTransferThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, job_name, source_base, dest_base, custom_source_path=None, copy_to_onedrive=False):
        super().__init__()
        self.job_name = job_name
        self.dest_base = dest_base
        self.custom_source_path = custom_source_path
        self.copy_to_onedrive = copy_to_onedrive
        
        # Set up source paths
        if custom_source_path:
            # Using a custom dropped folder
            self.source_label_data = os.path.join(custom_source_path, "Label Data")
            self.source_pix = os.path.join(custom_source_path, "Pix")
            self.source_job_folder = custom_source_path
        else:
            # Using OneDrive folder
            self.source_label_data = os.path.join(source_base, job_name, "Label Data")
            self.source_pix = os.path.join(source_base, job_name, "Pix")
            self.source_job_folder = os.path.join(source_base, job_name)
            
        self.dest_label_data = os.path.join(dest_base, "Label Data")
        self.dest_pix = os.path.join(dest_base, "Pix")
        
        # OneDrive destination for copying dropped folders
        username = os.environ.get('USERNAME', 'continental')
        self.onedrive_dest = os.path.join(f"C:\\Users\\{username}\\OneDrive\\Jobs", job_name)
        
    def run(self):
        try:
            # Validate source folders exist
            if not os.path.exists(self.source_label_data):
                self.finished.emit(False, f"Label Data folder not found in {os.path.basename(self.source_job_folder)}")
                return
            if not os.path.exists(self.source_pix):
                self.finished.emit(False, f"Pix folder not found in {os.path.basename(self.source_job_folder)}")
                return
            
            # Clear only Label Data directory
            self.progress.emit("Clearing Label Data directory...")
            self.clear_directory(self.dest_label_data)

            # Copy Label Data
            self.progress.emit("Copying Label Data files...")
            self.copy_directory(self.source_label_data, self.dest_label_data)

            # Copy Pix (without clearing first)
            self.progress.emit("Copying Pix files...")
            self.copy_directory_with_overwrite(self.source_pix, self.dest_pix)
            
            # If this is a custom source, also copy to OneDrive
            if self.copy_to_onedrive and self.custom_source_path:
                self.progress.emit("Copying job to OneDrive...")
                if os.path.exists(self.onedrive_dest):
                    self.progress.emit("Job already exists in OneDrive, skipping...")
                else:
                    shutil.copytree(self.source_job_folder, self.onedrive_dest)
                    self.progress.emit("Job copied to OneDrive successfully!")

            self.finished.emit(True, "Transfer completed successfully!")
        except Exception as e:
            self.finished.emit(False, f"Error during transfer: {str(e)}")
    
    def clear_directory(self, path):
        try:
            if os.path.exists(path):
                for filename in os.listdir(path):
                    file_path = os.path.join(path, filename)
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
        except Exception as e:
            raise Exception(f"Error clearing directory {path}: {str(e)}")
    
    def copy_directory(self, src, dst):
        try:
            if os.path.exists(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
        except Exception as e:
            raise Exception(f"Error copying from {src} to {dst}: {str(e)}")
    
    def copy_directory_with_overwrite(self, src, dst):
        try:
            if os.path.exists(src):
                if not os.path.exists(dst):
                    os.makedirs(dst)
                for item in os.listdir(src):
                    s = os.path.join(src, item)
                    d = os.path.join(dst, item)
                    if os.path.isfile(s):
                        shutil.copy2(s, d)  # This will overwrite if file exists
                    else:
                        if os.path.exists(d):
                            # If it's a directory that already exists, copy contents with overwrite
                            for sub_item in os.listdir(s):
                                s_path = os.path.join(s, sub_item)
                                d_path = os.path.join(d, sub_item)
                                if os.path.isfile(s_path):
                                    shutil.copy2(s_path, d_path)
                        else:
                            # If directory doesn't exist, copy the whole thing
                            shutil.copytree(s, d)
        except Exception as e:
            raise Exception(f"Error copying from {src} to {dst}: {str(e)}")

class JobManager(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Load the UI file
        ui_path = self.get_ui_path()
        uic.loadUi(ui_path, self)
        
        # Set window icon
        icon_path = self.get_icon_path()
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # Update window title with version
        self.setWindowTitle(f"Job File Manager v{CURRENT_VERSION}")
        
        # Set up paths
        self.username = os.environ.get('USERNAME', 'continental')
        self.source_path = f"C:\\Users\\{self.username}\\OneDrive\\Jobs"
        self.dest_path = "C:\\CADCode"
        
        # Track custom job sources (job_name -> custom_path)
        self.custom_job_sources = {}
        
        # Add drop zone to the UI
        self.add_drop_zone()
        
        # Initialize UI elements
        self.setup_ui()
        
        # Load jobs on startup
        self.refresh_jobs()
        
        # Check for updates after a short delay
        QTimer.singleShot(2000, self.check_for_updates)
        
    def get_ui_path(self):
        if hasattr(sys, '_MEIPASS'):
            # Running as bundled exe
            return os.path.join(sys._MEIPASS, 'job_manager.ui')
        else:
            # Running as script
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'job_manager.ui')
    
    def get_icon_path(self):
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, 'icon.ico')
        else:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
    
    def add_drop_zone(self):
        # Create and add the drop zone widget
        self.dropZone = DropZone()
        self.dropZone.fileDropped.connect(self.handle_dropped_folder)
        
        # Insert it after the instruction label
        self.centralwidget.layout().insertWidget(2, self.dropZone)
    
    def setup_ui(self):
        # Connect buttons
        self.refreshButton.clicked.connect(self.refresh_jobs)
        self.transferButton.clicked.connect(self.transfer_files)
        self.openSourceButton.clicked.connect(self.open_source_folder)
        self.openDestButton.clicked.connect(self.open_dest_folder)
        
        # Add Help menu with Check for Updates option
        menubar = self.menuBar()
        help_menu = menubar.addMenu('Help')
        
        check_update_action = help_menu.addAction('Check for Updates')
        check_update_action.triggered.connect(self.check_for_updates)
        
        about_action = help_menu.addAction('About')
        about_action.triggered.connect(self.show_about)
        
        # Disable transfer button initially
        self.transferButton.setEnabled(False)
        
        # Connect list selection
        self.jobListWidget.itemSelectionChanged.connect(self.on_selection_changed)
    
    def show_about(self):
        QMessageBox.about(self, "About Job Manager", 
                         f"Job Manager v{CURRENT_VERSION}\n\n"
                         "A tool for managing job file transfers between OneDrive and CADCode.")
    
    def check_for_updates(self):
        self.statusbar.showMessage("Checking for updates...")
        
        self.update_checker = UpdateChecker()
        self.update_checker.update_available.connect(self.handle_update_available)
        self.update_checker.error.connect(self.handle_update_error)
        self.update_checker.start()
    
    def handle_update_available(self, update_info):
        self.statusbar.showMessage("Update available!")
        
        release_notes = update_info.get('release_notes', 'No release notes available.')
        
        reply = QMessageBox.question(self, 'Update Available',
                                   f"Version {update_info['version']} is available!\n\n"
                                   f"Release notes:\n{release_notes}\n\n"
                                   "Would you like to download and install it?",
                                   QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            self.download_update(update_info['download_url'])
    
    def handle_update_error(self, error_msg):
        # Don't show error on startup check, only if manually checking
        if "Check for Updates" in [action.text() for action in self.menuBar().actions()[0].menu().actions() if action.isEnabled()]:
            self.statusbar.showMessage("Could not check for updates")
    
    def download_update(self, download_url):
        progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        
        self.downloader = UpdateDownloader(download_url)
        self.downloader.progress.connect(progress.setValue)
        self.downloader.finished.connect(lambda success, result: self.handle_download_finished(success, result, progress))
        self.downloader.start()
    
    def handle_download_finished(self, success, result, progress_dialog):
        progress_dialog.close()
        
        if success:
            reply = QMessageBox.question(self, 'Update Downloaded',
                                       'Update downloaded successfully!\n\n'
                                       'The application will now restart to apply the update.',
                                       QMessageBox.Ok | QMessageBox.Cancel)
            
            if reply == QMessageBox.Ok:
                self.apply_update(result)
        else:
            QMessageBox.critical(self, 'Download Failed',
                               f'Failed to download update:\n{result}')
    
    def apply_update(self, update_file):
        """Apply the update by replacing the current executable"""
        try:
            # Create a batch file to replace the executable
            batch_content = f"""@echo off
echo Updating Job Manager...
timeout /t 2 /nobreak > nul
move /y "{update_file}" "{sys.executable}"
start "" "{sys.executable}"
del "%~f0"
"""
            
            with open('update.bat', 'w') as f:
                f.write(batch_content)
            
            # Run the batch file and exit
            subprocess.Popen(['update.bat'], shell=True)
            QApplication.quit()
            
        except Exception as e:
            QMessageBox.critical(self, 'Update Failed',
                               f'Failed to apply update:\n{str(e)}')
        
    def handle_dropped_folder(self, folder_path):
        """Handle when a folder is dropped onto the drop zone"""
        job_name = os.path.basename(folder_path)
        
        # Validate the folder structure
        label_data_path = os.path.join(folder_path, "Label Data")
        pix_path = os.path.join(folder_path, "Pix")
        
        if not os.path.exists(label_data_path):
            self.statusbar.showMessage(f"Error: No 'Label Data' folder found in {job_name}")
            return
            
        if not os.path.exists(pix_path):
            self.statusbar.showMessage(f"Error: No 'Pix' folder found in {job_name}")
            return
        
        # Add to custom sources
        self.custom_job_sources[job_name] = folder_path
        
        # Add to list if not already there
        existing_items = [self.jobListWidget.item(i).text() for i in range(self.jobListWidget.count())]
        if job_name not in existing_items:
            self.jobListWidget.addItem(job_name)
            self.statusbar.showMessage(f"Added custom job: {job_name}")
        else:
            self.statusbar.showMessage(f"Updated custom source for: {job_name}")
        
        # Select the newly added item
        for i in range(self.jobListWidget.count()):
            if self.jobListWidget.item(i).text() == job_name:
                self.jobListWidget.setCurrentRow(i)
                break
    
    def refresh_jobs(self):
        # Clear only OneDrive jobs, keep custom ones
        current_custom_jobs = list(self.custom_job_sources.keys())
        
        self.jobListWidget.clear()
        
        # Re-add custom jobs first
        for job in current_custom_jobs:
            self.jobListWidget.addItem(f"📁 {job}")
        
        if not os.path.exists(self.source_path):
            self.statusbar.showMessage(f"OneDrive path not found: {self.source_path}")
            return
            
        try:
            jobs = [d for d in os.listdir(self.source_path) 
                   if os.path.isdir(os.path.join(self.source_path, d))]
            
            # Add OneDrive jobs
            for job in jobs:
                if job not in current_custom_jobs:
                    self.jobListWidget.addItem(job)
            
            total_jobs = self.jobListWidget.count()
            self.statusbar.showMessage(f"Found {total_jobs} job(s) ({len(current_custom_jobs)} custom)")
                
        except Exception as e:
            self.statusbar.showMessage(f"Error loading jobs: {str(e)}")
            
    def on_selection_changed(self):
        self.transferButton.setEnabled(bool(self.jobListWidget.selectedItems()))
        
    def transfer_files(self):
        selected_items = self.jobListWidget.selectedItems()
        if not selected_items:
            return
            
        job_text = selected_items[0].text()
        # Remove the custom job indicator if present
        job_name = job_text.replace("📁 ", "")
        
        # Check if this is a custom source job
        custom_source = self.custom_job_sources.get(job_name)
        copy_to_onedrive = custom_source is not None
        
        # Disable UI during transfer
        self.transferButton.setEnabled(False)
        self.refreshButton.setEnabled(False)
        self.jobListWidget.setEnabled(False)
        
        # Create and start transfer thread
        self.transfer_thread = FileTransferThread(
            job_name, 
            self.source_path, 
            self.dest_path,
            custom_source_path=custom_source,
            copy_to_onedrive=copy_to_onedrive
        )
        self.transfer_thread.progress.connect(self.update_status)
        self.transfer_thread.finished.connect(self.transfer_finished)
        self.transfer_thread.start()
        
    def update_status(self, message):
        self.statusbar.showMessage(message)
        
    def transfer_finished(self, success, message):
        # Re-enable UI
        self.transferButton.setEnabled(True)
        self.refreshButton.setEnabled(True)
        self.jobListWidget.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "Success", message)
            # If it was a custom job that was successfully copied to OneDrive, 
            # remove the custom indicator and clear from custom sources
            selected_items = self.jobListWidget.selectedItems()
            if selected_items:
                job_text = selected_items[0].text()
                if job_text.startswith("📁 "):
                    job_name = job_text.replace("📁 ", "")
                    if job_name in self.custom_job_sources:
                        del self.custom_job_sources[job_name]
                        # Refresh to show it as a regular OneDrive job now
                        self.refresh_jobs()
        else:
            QMessageBox.critical(self, "Error", message)
            
        self.statusbar.showMessage("Ready")
        
    def open_source_folder(self):
        selected_items = self.jobListWidget.selectedItems()
        if selected_items:
            job_text = selected_items[0].text()
            job_name = job_text.replace("📁 ", "")
            
            # Check if it's a custom source
            if job_name in self.custom_job_sources:
                path = self.custom_job_sources[job_name]
            else:
                path = os.path.join(self.source_path, job_name)
        else:
            path = self.source_path
            
        if os.path.exists(path):
            os.startfile(path)
        else:
            QMessageBox.warning(self, "Path Not Found", f"Cannot open: {path}")
            
    def open_dest_folder(self):
        if os.path.exists(self.dest_path):
            os.startfile(self.dest_path)
        else:
            QMessageBox.warning(self, "Path Not Found", f"Cannot open: {self.dest_path}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = JobManager()
    window.show()
    sys.exit(app.exec_())
