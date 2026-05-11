# ofi-lab-v3 Deployment Guide

Production deployment guide for ofi-lab-v3 direction prediction system.

## Prerequisites

- **OS**: Ubuntu 20.04 LTS or later
- **Python**: 3.13+ (with dev headers)
- **System packages**:
  ```bash
  sudo apt-get update
  sudo apt-get install -y python3.13 python3.13-dev python3.13-venv \
    sqlite3 libomp-dev nginx systemd curl git
  ```
- **libomp**: Required for LightGBM on non-x86 architectures
- **Disk space**: 10GB minimum for database and backups
- **Firewall**: Allow inbound 443 (HTTPS), 80 (HTTP for Let's Encrypt)

## One-Time Setup

### 1. Clone and prepare repository

```bash
cd /opt
sudo git clone https://github.com/your-org/ofi-lab-v3.git
sudo chown -R v3:v3 /opt/ofi-lab-v3
cd /opt/ofi-lab-v3
```

### 2. Create virtual environment and install dependencies

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install uvicorn  # For dashboard API
```

### 3. Generate admin secret

```bash
SECRET=$(./scripts/generate_admin_secret.sh)
echo "Generated admin secret: $SECRET"
# Save this somewhere secure (password manager, etc.)
```

### 4. Configure environment

```bash
sudo mkdir -p /etc/v3 /data /backups
sudo cp deploy/systemd/env.example /etc/v3/env
sudo nano /etc/v3/env  # Edit with your settings
```

**Required settings in `/etc/v3/env`:**

```bash
V3_ENV=prod
V3_ADMIN_SECRET=<paste-from-step-3>
V3_DB_PATH=/data/v3.db
V3_KILL_SWITCH_PATH=/data/kill_switch.json
BYBIT_API_KEY=<your-key>
BYBIT_API_SECRET=<your-secret>
```

**Optional settings:**

```bash
DASHBOARD_USER=admin
DASHBOARD_PASS=<strong-password>  # Only if you want HTTP Basic Auth
LOG_LEVEL=INFO
ENABLE_LIVE_TRADING=false  # Start with false, enable only after testing
```

```bash
sudo chown v3:v3 /etc/v3/env
sudo chmod 600 /etc/v3/env
```

### 5. Create system user and directories

```bash
sudo useradd -r -s /bin/false -d /opt/ofi-lab-v3 v3 2>/dev/null || true
sudo chown -R v3:v3 /opt/ofi-lab-v3 /data /etc/v3
sudo chmod 700 /data /etc/v3
```

### 6. Initialize database

```bash
source .venv/bin/activate
./scripts/init_db.py
```

Verify database was created:
```bash
sqlite3 /data/v3.db "SELECT name FROM sqlite_master WHERE type='table';"
```

## Install Systemd Services

### 1. Copy service files

```bash
sudo cp deploy/systemd/v3-dashboard.service /etc/systemd/system/
sudo cp deploy/systemd/v3-paper-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 2. Enable services

```bash
sudo systemctl enable v3-dashboard.service
sudo systemctl enable v3-paper-trader.service
```

## Install Nginx Reverse Proxy

### 1. Copy nginx config

```bash
sudo cp deploy/nginx/v3-dashboard.conf /etc/nginx/sites-available/v3-dashboard
sudo nano /etc/nginx/sites-available/v3-dashboard
# Edit: Change server_name to your domain
```

### 2. Enable site and reload

```bash
sudo ln -s /etc/nginx/sites-available/v3-dashboard /etc/nginx/sites-enabled/
sudo nginx -t  # Test config
sudo systemctl reload nginx
```

### 3. Setup SSL (Let's Encrypt)

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot certonly --standalone -d dashboard.example.com
```

Update `/etc/nginx/sites-available/v3-dashboard` with certificate paths, then reload:
```bash
sudo systemctl reload nginx
```

## Install Database Backup Cron

### 1. Copy backup script

```bash
sudo cp deploy/cron/v3-db-backup.sh /opt/ofi-lab-v3/deploy/cron/
sudo chown v3:v3 /opt/ofi-lab-v3/deploy/cron/v3-db-backup.sh
sudo chmod 755 /opt/ofi-lab-v3/deploy/cron/v3-db-backup.sh
```

### 2. Add to crontab

```bash
# As root:
sudo crontab -e

# Add line:
0 */6 * * * /opt/ofi-lab-v3/deploy/cron/v3-db-backup.sh >> /var/log/v3-backup.log 2>&1
```

Verify cron job:
```bash
sudo crontab -l
```

## Run Preflight Checks

Before starting services, run the preflight validation:

```bash
source .venv/bin/activate
./scripts/preflight_v3.py
```

Expected output:
```
✓ Environment variables set
✓ Database initialized and accessible
✓ All required tables present
✓ API credentials configured
✓ Admin secret validation passed
```

If any check fails, fix the issue and re-run.

## Start Services

```bash
sudo systemctl start v3-dashboard.service
sudo systemctl start v3-paper-trader.service
```

Check status:
```bash
sudo systemctl status v3-dashboard.service
sudo systemctl status v3-paper-trader.service
```

## Verify Deployment

### 1. Check logs

```bash
# Dashboard logs
sudo journalctl -u v3-dashboard -n 20 -f

# Paper trader logs
sudo journalctl -u v3-paper-trader -n 20 -f
```

### 2. Test dashboard API

```bash
# Test locally (if no HTTP auth)
curl http://127.0.0.1:8081/api/status

# Test through nginx (with domain)
curl https://dashboard.example.com/api/status

# Test with HTTP Basic Auth (if configured)
curl -u admin:password https://dashboard.example.com/api/status
```

### 3. Test kill switch

```bash
curl https://dashboard.example.com/api/kill_switch
```

Expected response (if disengaged):
```json
{"engaged": false, "state": {...}}
```

### 4. Monitor background tasks

```bash
# Check paper trader is running
ps aux | grep paper_trader

# Check dashboard is listening
netstat -tlnp | grep 8081
```

## Troubleshooting

### Dashboard won't start

**Check logs:**
```bash
sudo journalctl -u v3-dashboard -n 50
```

**Common issues:**

1. **Admin secret not set (V3_ENV=prod):**
   ```
   ValueError: V3_ADMIN_SECRET environment variable is not set but V3_ENV=prod
   ```
   Fix: Set `V3_ADMIN_SECRET` in `/etc/v3/env`

2. **Database not found:**
   ```
   FileNotFoundError: [Errno 2] No such file or directory: '/data/v3.db'
   ```
   Fix: Run `./scripts/init_db.py`

3. **Permission denied:**
   ```
   PermissionError: [Errno 13] Permission denied: '/data'
   ```
   Fix: Ensure v3 user owns /data: `sudo chown v3:v3 /data`

### Paper trader crashes

**Check logs:**
```bash
sudo journalctl -u v3-paper-trader -n 50
```

**Common issues:**

1. **API credentials invalid:**
   Check `BYBIT_API_KEY` and `BYBIT_API_SECRET` in `/etc/v3/env`

2. **Market data unavailable:**
   Verify Bybit API is accessible: `curl https://api.bybit.com/v5/market/time`

### Nginx SSL issues

**Test nginx config:**
```bash
sudo nginx -t
```

**Check SSL certificate:**
```bash
openssl x509 -in /etc/letsencrypt/live/dashboard.example.com/fullchain.pem -text -noout
```

**Renew certificate:**
```bash
sudo certbot renew --dry-run
sudo certbot renew
```

### Database corruption

**Backup and verify:**
```bash
sqlite3 /data/v3.db "PRAGMA integrity_check;"
```

If corrupted, restore from backup (see Rollback section below).

## Monitoring

### Setup log aggregation

Forward systemd logs to a remote syslog server:

```bash
# /etc/rsyslog.d/v3-dashboard.conf
:programname, isequal, "v3-dashboard" @@syslog.example.com:514
:programname, isequal, "v3-paper" @@syslog.example.com:514
```

Reload rsyslog:
```bash
sudo systemctl reload rsyslog
```

### Setup alerts

Configure monitoring for:
- Service down (systemd notification)
- High error rate (parse journalctl)
- Disk space low (cron job)
- Database backup failures (check mtime of `/backups/v3-*.db.gz`)

Example cron alert:
```bash
# Check backup freshness (older than 12 hours = alert)
*/30 * * * * test ! -f /backups/v3-*.db.gz -o $(find /backups -name "v3-*.db.gz" -mtime +0.5) && mail -s "v3 backup failed" ops@example.com
```

## Rollback

If deployment fails or needs rollback:

### 1. Stop services

```bash
sudo systemctl stop v3-dashboard.service v3-paper-trader.service
```

### 2. List available backups

```bash
ls -lh /backups/v3-*.db.gz
```

### 3. Restore database

```bash
# Pick latest backup before failure
BACKUP=/backups/v3-20240510-120000.db.gz
zcat $BACKUP | sqlite3 /data/v3.db
```

### 4. Verify restore

```bash
sqlite3 /data/v3.db "SELECT COUNT(*) FROM registry_audit;" # Should be > 0
```

### 5. Restart services

```bash
sudo systemctl start v3-dashboard.service v3-paper-trader.service
```

## Maintenance

### Regular tasks

**Daily:**
- Monitor logs for errors
- Check disk space: `df -h`

**Weekly:**
- Review kill switch status
- Check model performance metrics
- Verify backups exist

**Monthly:**
- Review audit log: `sqlite3 /data/v3.db "SELECT * FROM registry_audit LIMIT 10;"`
- Test rollback procedure
- Update system packages: `sudo apt update && sudo apt upgrade`

### Backup verification

```bash
# Test restore (don't overwrite production)
TEST_DB=/tmp/restore-test.db
zcat /backups/v3-latest.db.gz | sqlite3 $TEST_DB
sqlite3 $TEST_DB "SELECT COUNT(*) FROM registry_audit;"
rm $TEST_DB
```

### Update deployment

To update code:

```bash
cd /opt/ofi-lab-v3
sudo systemctl stop v3-dashboard.service v3-paper-trader.service
git fetch origin
git checkout v3.1.0  # Or desired version
source .venv/bin/activate
pip install -r requirements.txt
./scripts/init_db.py  # Apply any migrations
sudo systemctl start v3-dashboard.service v3-paper-trader.service
```

## Support

For issues, check:

1. **Logs**: `sudo journalctl -u v3-dashboard -f`
2. **Config**: `cat /etc/v3/env`
3. **Database**: `sqlite3 /data/v3.db "PRAGMA integrity_check;"`
4. **Preflight**: `./scripts/preflight_v3.py`
5. **Health endpoint**: `curl https://dashboard.example.com/api/status`

## Security Notes

- **Restrict `/etc/v3/env`**: Contains secrets (mode 600)
- **Backup directory**: Only readable by v3 user (mode 700)
- **Database**: Only accessible by v3 user
- **Nginx**: Behind firewall, HTTPS only
- **SSH keys**: Protect deploy key, rotate API credentials quarterly

## Additional Resources

- ofi-lab-v3 documentation: See `/opt/ofi-lab-v3/docs/`
- Systemd documentation: `man systemd.service`
- Nginx documentation: https://nginx.org/en/docs/
- SQLite backup: https://www.sqlite.org/backup.html
