# Blitz Prints Live

A dynamic version of the Blitz Prints 3D-printing shop (CCEN 448, Lab 5).
Visitors create an account, browse the catalogue and place orders. Accounts, products and orders
are stored in PostgreSQL.

- **Web:** Python 3.12, Flask and Gunicorn on port 8000
- **Database:** PostgreSQL 16 in its own container, data kept in the `pgdata` volume
- **Auth:** scrypt password hashes, signed session cookies, CSRF tokens on every form

## Requirements

Docker Engine with the Compose plugin. On Ubuntu 24.04:

```bash
sudo apt-get install -y docker.io docker-compose-v2
```

## Install

```bash
git clone https://github.com/blitzict/blitzprints-live.git
cd blitzprints-live
cp .env.example .env
# set POSTGRES_PASSWORD and SECRET_KEY in .env, for example:
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/; s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/" .env
sudo docker compose up -d --build
```

The tables are created and the catalogue is loaded on first start. Open `http://<server-ip>:8000`.

## Useful commands

```bash
sudo docker compose ps                     # service status
sudo docker compose logs -f web            # web logs
sudo docker compose exec db psql -U blitz -d blitzprints   # database client
```

## Behind a proxy or tunnel

The app trusts one proxy hop (`X-Forwarded-For`, `-Proto`, `-Host`). It builds only relative links,
and the session cookie isn't bound to a host. It works unchanged at `http://<ip>:8000`, through an SSH port forward, or at
an HTTPS tunnel URL.
