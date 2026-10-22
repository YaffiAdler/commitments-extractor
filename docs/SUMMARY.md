# Summary

## Lab system check (demo)

After a job finishes, the reviewer can check its commitments against the samples the lab system received ("בדיקה מול מערכת המעבדה"). The match is by central number only and shows three groups: matched, samples without a commitment, and commitments without a sample. It changes no field.

**Simulated connection; for a real connection implement `LabSystemConnector` against the lab system.** The demo connector (`CE_LAB_CONNECTOR=demo`) reads dummy data from `backend/data/demo_lab_samples.json`, and the screen marks the result "מצב הדגמה: נתונים מדומים". Details: README, assumptions 117–119.
