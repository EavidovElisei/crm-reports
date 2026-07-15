---
name: deploy-crm-reports
description: Safely releases CRM Reports to the production server with rsync, a remote backup, service-only restart, health check, and rollback. Use when the user asks to deploy, publish, release, or roll back CRM Reports.
disable-model-invocation: true
---

# Deploy CRM Reports

Deploy only the CRM Reports application. Do not edit, validate, reload, restart, or otherwise change Nginx, PostgreSQL, or other applications.

## Production target

- SSH: `davidov@192.168.5.66`
- Application: `/home/kichagin/crm_reports`
- Service: `crm-reports.service`
- Release user: `davidov`

Never write passwords, tokens, proxy URLs with credentials, or production configuration values to Git, commands, logs, or this skill. Let SSH and `sudo` prompt interactively.

## Preflight

1. Confirm the current branch and commit are the intended release.
2. Inspect the service without changing it:

   ```bash
   ssh davidov@192.168.5.66 'systemctl is-active crm-reports'
   ```

3. Review the exact files that would be transferred:

   ```bash
   rsync -aivn \
     --exclude='.git/' --exclude='.venv/' --exclude='logs/' --exclude='backup/' \
     --exclude='__pycache__/' --exclude='*.pyc' --exclude='.DS_Store' \
     --exclude='config_production.py' --exclude='config_local.py' \
     --exclude='.env' --exclude='.env.*' --exclude='deployment_info.txt' \
     --exclude='deploy.sh' \
     -e ssh ./ davidov@192.168.5.66:/home/kichagin/crm_reports/
   ```

Stop and ask for confirmation if the preview includes a secret, a production-only configuration file, or an unexpected deletion. Never add `--delete`.

## Release

1. Create a timestamped remote backup of files before replacing them:

   ```bash
   ssh davidov@192.168.5.66 \
     'set -eu; release=$(date +%Y%m%d-%H%M%S); mkdir -p "/home/kichagin/crm_reports/backup/releases/$release"; cp -a /home/kichagin/crm_reports/main.py /home/kichagin/crm_reports/config.py /home/kichagin/crm_reports/scheduler.py /home/kichagin/crm_reports/reporters /home/kichagin/crm_reports/workers "/home/kichagin/crm_reports/backup/releases/$release/"'
   ```

2. Run the reviewed `rsync` command again without `-n`.
3. Restart only CRM Reports:

   ```bash
   ssh -t davidov@192.168.5.66 'sudo systemctl restart crm-reports && sudo systemctl is-active --quiet crm-reports'
   ```

4. Verify the local application endpoint and inspect only this service's recent logs if it fails:

   ```bash
   ssh davidov@192.168.5.66 'curl --fail --silent --show-error http://127.0.0.1:5000/ >/dev/null && echo healthy'
   ssh davidov@192.168.5.66 'sudo journalctl -u crm-reports -n 100 --no-pager'
   ```

## Rollback

If the service or health check fails, identify the release timestamp and restore only the application files from that backup. Then restart only `crm-reports.service` and repeat the health check. Do not touch Nginx.

```bash
ssh -t davidov@192.168.5.66 'set -eu; release=REPLACE_WITH_TIMESTAMP; cp -a "/home/kichagin/crm_reports/backup/releases/$release/." /home/kichagin/crm_reports/; sudo systemctl restart crm-reports; sudo systemctl is-active --quiet crm-reports'
```
