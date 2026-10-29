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
- Application health endpoint: `http://127.0.0.1:8080/`

Never write passwords, tokens, proxy URLs with credentials, or production configuration values to Git, commands, logs, or this skill. Let SSH and `sudo` prompt interactively.

## Preflight

1. Confirm the current branch and commit are the intended release.
2. Inspect the service without changing it:

   ```bash
   ssh davidov@192.168.5.66 'systemctl is-active crm-reports'
   ```

3. Define only the reviewed release files and preview their transfer. Do not sync the whole working directory:

   ```bash
   rsync -aivn -e ssh path/to/changed_file.py \
     davidov@192.168.5.66:/home/kichagin/crm_reports/path/to/
   ```

Stop and ask for confirmation if the preview includes a secret, a production-only configuration file, or an unexpected file. Never add `--delete`.

## Release

1. Create a timestamped remote backup of exactly the files to replace. The directory is writable by the release user:

   ```bash
   ssh davidov@192.168.5.66 \
     'set -eu; release=$(date +%Y%m%d-%H%M%S); backup="/home/kichagin/crm_reports/.release-backups/$release"; mkdir -p "$backup"; for file in path/to/changed_file.py; do mkdir -p "$backup/$(dirname "$file")"; cp -a "/home/kichagin/crm_reports/$file" "$backup/$file"; done; printf "%s\n" "$release"'
   ```

2. Run the reviewed `rsync` command again without `-n`, once for each release file.
3. Restart only CRM Reports:

   ```bash
   ssh davidov@192.168.5.66 'sudo -n /usr/bin/systemctl restart crm-reports && sudo -n /usr/bin/systemctl is-active crm-reports'
   ```

4. Verify the local application endpoint and inspect only this service's recent logs if it fails:

   ```bash
   ssh davidov@192.168.5.66 'curl --fail --silent --show-error http://127.0.0.1:8080/ >/dev/null && echo healthy'
   ssh davidov@192.168.5.66 'sudo -n /usr/bin/journalctl -u crm-reports -n 100 --no-pager'
   ```

## Rollback

If the service or health check fails, identify the release timestamp and restore only the application files from that backup. Then restart only `crm-reports.service` and repeat the health check. Do not touch Nginx.

```bash
ssh davidov@192.168.5.66 'set -eu; release=REPLACE_WITH_TIMESTAMP; backup="/home/kichagin/crm_reports/.release-backups/$release"; for file in path/to/changed_file.py; do cp -a "$backup/$file" "/home/kichagin/crm_reports/$file"; done; sudo -n /usr/bin/systemctl restart crm-reports; sudo -n /usr/bin/systemctl is-active crm-reports'
```
