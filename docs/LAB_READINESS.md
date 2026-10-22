# Lab readiness

What is still needed before the engine can be used with the lab's own systems.

| Area | Status | What is needed |
|---|---|---|
| Lab system connection (`GET /jobs/{id}/lab-match`) | Simulated: `DemoLabConnector` reads dummy data | Implement `app/lab_system/base.LabSystemConnector` against the lab system, add it to `CE_LAB_CONNECTOR`, and agree with the lab on the date range and on central number format (leading zeros) |
