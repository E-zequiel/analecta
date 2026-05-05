"""First-run onboarding dialog — shown once when no config file exists."""

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from analecta.config import AppConfig


class FirstRunDialog(QDialog):
    """One-time setup dialog presented on the very first launch.

    Asks the user where to store their vault. Pre-fills the default path.
    Closing without confirming is not possible — the button is the only exit.

    Attributes:
        result_config: ``AppConfig`` built from the chosen vault path.
            Valid only after ``exec()`` returns.

    Args:
        parent: Parent QWidget.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Analecta")
        self.setMinimumWidth(480)
        self.result_config: AppConfig = AppConfig()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(28, 24, 28, 24)

        layout.addWidget(
            QLabel(
                "<b>Welcome to Analecta.</b><br><br>"
                "Choose where your vault will be stored. "
                "This is the directory where all your extracted articles "
                "and assets will live."
            )
        )

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setText(str(AppConfig().vault_path))
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self._path_edit)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        start_btn = QPushButton("Get Started")
        start_btn.setDefault(True)
        start_btn.clicked.connect(self._confirm)
        layout.addWidget(start_btn)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Vault Directory",
            self._path_edit.text() or str(Path.home()),
        )
        if chosen:
            self._path_edit.setText(chosen)

    def _confirm(self) -> None:
        self.result_config = AppConfig(vault_path=Path(self._path_edit.text()))
        self.accept()
