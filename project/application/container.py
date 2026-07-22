"""Single composition root for application services."""

from pathlib import Path
from typing import Optional

from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from config.settings import Settings, settings as default_settings
from infrastructure.repositories.case_library import CaseLibraryManager


class ApplicationContainer:
    """Build the project-scoped service graph used by Streamlit and scripts."""

    def __init__(self, settings: Settings = default_settings) -> None:
        self.settings = settings

    def build_ui_service(
        self, project_db: Path, library_db: Optional[Path] = None
    ) -> UIApplicationService:
        manager = ProjectManager(project_db)
        library = CaseLibraryManager(library_db) if library_db else None
        return UIApplicationService(manager, library, self.settings)
