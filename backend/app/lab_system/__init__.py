"""Lab system connectors behind the ``LabSystemConnector`` interface."""
from __future__ import annotations

from app.config import LabConnectorName, Settings
from app.lab_system.base import LabSystemConnector
from app.lab_system.demo import DemoLabConnector


def create_lab_connector(settings: Settings) -> LabSystemConnector:
    """Build the connector selected in ``settings.lab_connector``."""
    if settings.lab_connector is LabConnectorName.DEMO:
        return DemoLabConnector(settings.lab_demo_samples_path)
    raise ValueError(f"unknown lab connector: {settings.lab_connector}")
