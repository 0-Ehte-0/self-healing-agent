# Verification Requirements — SCN-001

During the 90-second post-remediation stabilization window:
1. Container status is `running`.
2. `/health/ready` returns HTTP 200 for three consecutive checks at 15s intervals.
3. HTTP error rate is `< 2%`.
4. `ContainerDown` alert is inactive.