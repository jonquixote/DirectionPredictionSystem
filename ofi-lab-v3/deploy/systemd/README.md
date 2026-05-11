# Systemd Units for ofi-lab-v3

This directory contains systemd service unit files for running ofi-lab-v3 components in production.

## Services

### v3-paper-trader.service
Runs the paper trading system that executes trades on a simulated account.

### v3-dashboard.service
Runs the dashboard API (FastAPI) on localhost:8080. Should be proxied through nginx.

## Installation

1. **Copy service files to systemd directory:**
   ```bash
   sudo cp v3-paper-trader.service /etc/systemd/system/
   sudo cp v3-dashboard.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

2. **Create system user and directories:**
   ```bash
   sudo useradd -r -s /bin/false v3
   sudo mkdir -p /data /etc/v3
   sudo chown v3:v3 /data /etc/v3
   sudo chmod 700 /data /etc/v3
   ```

3. **Configure environment:**
   ```bash
   sudo cp env.example /etc/v3/env
   sudo nano /etc/v3/env  # Edit with your settings
   sudo chown v3:v3 /etc/v3/env
   sudo chmod 600 /etc/v3/env
   ```

4. **Generate admin secret:**
   ```bash
   SECRET=$(scripts/generate_admin_secret.sh)
   sudo tee -a /etc/v3/env > /dev/null <<EOF
   V3_ADMIN_SECRET=$SECRET
   EOF
   ```

5. **Enable and start services:**
   ```bash
   sudo systemctl enable v3-paper-trader.service
   sudo systemctl enable v3-dashboard.service
   sudo systemctl start v3-paper-trader.service
   sudo systemctl start v3-dashboard.service
   ```

## Verification

Check service status:
```bash
sudo systemctl status v3-paper-trader.service
sudo systemctl status v3-dashboard.service
```

View logs:
```bash
sudo journalctl -u v3-paper-trader -f
sudo journalctl -u v3-dashboard -f
```

Test dashboard API:
```bash
# Without auth (dev mode):
curl http://127.0.0.1:8080/api/kill_switch

# With auth (prod mode):
curl -u admin:password http://127.0.0.1:8080/api/kill_switch
```

## Troubleshooting

### Service fails to start
1. Check logs: `sudo journalctl -u v3-dashboard -n 50`
2. Verify environment: `cat /etc/v3/env`
3. Ensure working directory exists: `/opt/ofi-lab-v3`
4. Ensure user has permissions: `ls -la /data`

### Admin secret validation fails
- In production, ensure `V3_ADMIN_SECRET` is set in `/etc/v3/env`
- Use `scripts/generate_admin_secret.sh` to create a new secret
- Confirm it's hex-encoded, ~64 characters

### Dashboard can't connect to database
- Check `V3_DB_PATH` in `/etc/v3/env`
- Ensure directory exists and is writable: `ls -la /data`
- Run init script: `/opt/ofi-lab-v3/scripts/init_db.py`

## Stopping and Restarting

```bash
# Graceful restart
sudo systemctl restart v3-dashboard.service

# Stop both services
sudo systemctl stop v3-paper-trader.service v3-dashboard.service

# View all ofi-lab-v3 processes
sudo systemctl status 'v3-*'
```

## Rollback

If deployment fails:
1. Stop services: `sudo systemctl stop v3-dashboard v3-paper-trader`
2. Restore database from backup: `sqlite3 /data/v3.db < /backups/v3-YYYYMMDD-HHMM.db.gz`
3. Verify backup integrity before restarting
4. Start services: `sudo systemctl start v3-dashboard v3-paper-trader`

## SELinux / AppArmor

If using SELinux or AppArmor, you may need to create profiles for:
- `/opt/ofi-lab-v3` (read/execute)
- `/data` (read/write)
- `/etc/v3/env` (read)

Consult your distribution's security documentation.
