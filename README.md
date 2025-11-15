# Déployer GLPI Oracle Docker

**User:** Ahmed-Foued HAMED (ahmed.foued@gmail.com)  
**Created:** 11/14/2025 19:21:24  
**Updated:** 11/15/2025 17:39:02  
**Exported:** 11/15/2025 17:43:31  
**Link:** [https://chatgpt.com/g/g-p-691772eadedc8191b9357a38ad70426d-glpi/c/69177323-340c-8330-99e6-b4b4e77dc2d6](https://chatgpt.com/g/g-p-691772eadedc8191b9357a38ad70426d-glpi/c/69177323-340c-8330-99e6-b4b4e77dc2d6)  

## Prompt:
Réflexion durant 26s

Parfait, on commence à avoir un joli petit produit là 😎

Je vais faire deux choses :

1.  Te donner **la documentation technique complète**, propre, à jour (avec les derniers changements : `phone_number`, `auth`, gestion d’erreurs, addendum AlmaLinux/Docker, etc.).
2.  Te donner **un pas-à-pas Git/GitHub** pour versionner ce script depuis ta VPS.

* * *

1\. Documentation technique – POC Oracle → GLPI
===============================================

1.1. Objectif
-------------

Mettre en place un **POC de synchronisation** entre :

*   une base **Oracle XE** (table `employees`)
*   une instance **GLPI** (en Docker)

via un **script Python** qui consomme l’API REST GLPI et synchronise les utilisateurs.

Contraintes :

*   Aucun conteneur ne tourne en root :
    *   user système `glpi` pour GLPI + script,
    *   user système `oracle` pour Oracle.
*   L’API REST GLPI est **la seule porte d’entrée** : on ne modifie pas directement la base MySQL GLPI.

* * *

1.2. Architecture globale
-------------------------

### 1.2.1. Schéma (vue logique)

ASCII en noir & blanc :

```text
+--------------------------+             +-----------------------------+
|   Poste Admin / DBeaver  |             |       Poste Admin GLPI      |
|  (navigateur, DBeaver)   |             |      (navigateur web)       |
+------------+-------------+             +--------------+--------------+
             | HTTP 8080 / Oracle 1521                  |
             |                                          |
             v                                          v
        +----+------------------------------------------+----+
        |             VPS AlmaLinux (IONOS)                  |
        |                                                    |
        |  +-------------------+   +---------------------+   |
        |  |   User system     |   |     User system     |   |
        |  |      glpi         |   |       oracle        |   |
        |  +----------+--------+   +----------+----------+   |
        |             |                       |              |
        |   /opt/glpi-stack                  /opt/oracle-stack
        |   /opt/glpi-oracle-sync                            |
        |             |                       |              |
        |   +-------------------+   +---------------------+  |
        |   | Docker GLPI       |   | Docker Oracle XE    |  |
        |   |  - glpi/glpi      |   |  - gvenzl/oracle-xe |  |
        |   |  - mysql:8.0      |   |                     |  |
        |   +---------+---------+   +----------+----------+  |
        |             |                        |             |
        +-------------+------------------------+-------------+
                      | HTTP 8080 (API REST)   |
                      v                        |
           +-------------------------+         |
           | Script Python           |         |
           | /opt/glpi-oracle-sync  |---------+
           | - oracle_to_glpi_sync.py
           | - .env
           | - field_mapping.json
           +-------------------------+
```

* * *

2\. Préparation de la VPS AlmaLinux
-----------------------------------

### 2.1. Création des utilisateurs système

```bash
# En root (ou via sudo)
useradd -m -s /bin/bash glpi
passwd glpi

useradd -m -s /bin/bash oracle
passwd oracle
```

### 2.2. Installation de Docker & docker compose

```bash
sudo dnf install -y yum-utils device-mapper-persistent-data lvm2
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo systemctl enable --now docker
sudo systemctl status docker   # doit être "active (running)"
```

### 2.3. Groupe `docker` et droits

```bash
getent group docker || sudo groupadd docker

sudo usermod -aG docker glpi
sudo usermod -aG docker oracle
```

> Se déconnecter / reconnecter pour que les droits prennent effet, puis vérifier :

```bash
su - glpi
docker ps   # doit fonctionner sans sudo
```

### 2.4. Arborescence `/opt`

```bash
sudo mkdir -p /opt/glpi-stack
sudo mkdir -p /opt/oracle-stack
sudo mkdir -p /opt/glpi-oracle-sync

sudo chown glpi:glpi /opt/glpi-stack
sudo chown oracle:oracle /opt/oracle-stack
sudo chown glpi:glpi /opt/glpi-oracle-sync
```

### 2.5. Pare-feu IONOS

Au niveau de la console IONOS, ouvrir :

*   `8080/tcp` → accès GLPI + API REST
*   `1521/tcp` → accès Oracle (DBeaver, etc.)

Il n’y a pas de `firewalld` actif sur la VPS dans ton cas, donc seul le pare-feu IONOS compte.

* * *

3\. Déploiement GLPI (user `glpi`)
----------------------------------

### 3.1. Fichiers GLPI

```bash
su - glpi
cd /opt/glpi-stack
```

**`.env` de GLPI :**

```bash
cat > .env << 'EOF'
GLPI_DB_HOST=db
GLPI_DB_PORT=3306
GLPI_DB_NAME=glpi
GLPI_DB_USER=glpi
GLPI_DB_PASSWORD=glpi
EOF
```

**`docker-compose.yml` :**

```bash
cat > docker-compose.yml << 'EOF'
services:
  glpi:
    image: glpi/glpi:latest
    container_name: glpi
    restart: unless-stopped
    volumes:
      - "./storage/glpi:/var/glpi:rw"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8080:80"

  db:
    image: mysql:8.0
    container_name: glpi-mysql
    restart: unless-stopped
    command: --default-authentication-plugin=mysql_native_password
    environment:
      MYSQL_RANDOM_ROOT_PASSWORD: "yes"
      MYSQL_DATABASE: ${GLPI_DB_NAME}
      MYSQL_USER: ${GLPI_DB_USER}
      MYSQL_PASSWORD: ${GLPI_DB_PASSWORD}
    volumes:
      - ./mysql:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "-u", "${GLPI_DB_USER}", "--password=${GLPI_DB_PASSWORD}"]
      start_period: 5s
      interval: 5s
      timeout: 5s
      retries: 10
EOF
```

### 3.2. Lancement GLPI

```bash
docker compose up -d
docker compose ps
ss -lntp | grep 8080
curl -v http://localhost:8080/ 2>&1 | head -n 20
```

Depuis ton poste :

```text
http://IP_VPS:8080/
```

* * *

4\. Déploiement Oracle XE (user `oracle`)
-----------------------------------------

### 4.1. Fichiers Oracle

```bash
su - oracle
cd /opt/oracle-stack
```

**`.env` Oracle :**

```bash
cat > .env << 'EOF'
# Mot de passe pour SYS / SYSTEM
ORACLE_PASSWORD=OraclePwd1!

# User applicatif pour ton POC
APP_USER=EMP_APP
APP_USER_PASSWORD=EmpAppPwd1!
EOF
```

**Répertoire données avec bons droits :**

```bash
mkdir -p /opt/oracle-stack/oradata
# 54321 est l'UID utilisé par l'image gvenzl/oracle-xe
chown -R 54321:54321 /opt/oracle-stack/oradata
```

**`docker-compose.yml` :**

```bash
cat > docker-compose.yml << 'EOF'
services:
  oracle:
    image: gvenzl/oracle-xe:21-slim
    container_name: oracle-xe
    restart: unless-stopped
    env_file: .env
    ports:
      - "1521:1521"
    volumes:
      - ./oradata:/opt/oracle/oradata
EOF
```

### 4.2. Lancement Oracle XE

```bash
docker compose up -d
docker compose ps
ss -lntp | grep 1521
```

### 4.3. Connexion et table `employees`

Depuis DBeaver (poste admin) :

*   Host : `IP_VPS`
*   Port : `1521`
*   Service : `XEPDB1`
*   User : `EMP_APP`
*   Password : `EmpAppPwd1!`

Crée la table `employees` + data (exemple) :

```sql
CREATE TABLE employees (
  employee_code   VARCHAR2(50) PRIMARY KEY,
  first_name      VARCHAR2(100),
  last_name       VARCHAR2(100),
  email           VARCHAR2(200),
  phone_number    VARCHAR2(50),
  department      VARCHAR2(100),
  job_title       VARCHAR2(100),
  status          VARCHAR2(20)  -- ACTIVE / INACTIVE
);

-- Exemple d'inserts
INSERT INTO employees (employee_code, first_name, last_name, email, phone_number, department, job_title, status)
VALUES ('E0001', 'Alice', 'Durand', 'alice.durand@example.com', '0600000001', 'IT', 'Engineer', 'ACTIVE');
COMMIT;
```

* * *

5\. Configuration API REST GLPI
-------------------------------

### 5.1. Pourquoi l’API REST et pas MySQL GLPI direct ?

*   GLPI gère beaucoup de logique métier dans PHP :
    *   profils, entités, droits, historique, plugins…
*   Le schéma MySQL **évolue** entre versions.
*   Écrire directement dans `glpi_users` & co est fragile :
    *   risque de casser l’intégrité,
    *   incompatibilités futures.

**Bonne pratique** : passer par l’API REST officielle, qui reste stable, documentée, et applique toutes les règles GLPI côté serveur.

### 5.2. Activation de l’API REST

Dans GLPI (en web) :

1.  Se connecter en **super-admin**.
2.  Menu **Configuration → Général → API**.
3.  Activer l’API REST.
4.  Créer un **client API** :
    *   noter l’`App Token`.

### 5.3. Création de l’utilisateur technique

1.  Menu **Administration → Utilisateurs**.
2.  Créer un user, par ex. : `oracle_sync`.
3.  Lui donner un profil avec les droits nécessaires pour gérer les `User`.
4.  Lui permettre l’accès à l’API (cocher l’option).
5.  Générer un **User Token**.

Tu te retrouves avec :

*   `GLPI_APP_TOKEN` = token du client API
*   `GLPI_USER_TOKEN` = token de l’utilisateur `oracle_sync`

### 5.4. Test API via curl

Depuis la VPS (user `glpi` ou autre) :

```bash
curl -s -X GET \
  -H "App-Token: 3vuDiA6NsvCW6qp9uRUr9ZuieuoJNukZ8aCerWu5" \
  -H "Authorization: user_token 6LbzOJZ0kgod7dqgQQGLvBxDbM1iJra7z9A3Orum" \
  "http://82.165.217.9:8080/apirest.php/initSession"
```

Si tout est bon : réponse JSON avec `session_token`.

* * *

6\. Script de synchro Oracle → GLPI
-----------------------------------

### 6.1. Arborescence

```text
/opt/glpi-oracle-sync
  ├── .env
  ├── field_mapping.json
  ├── oracle_to_glpi_sync.py
  └── sync.log         (généré par le script)
```

### 6.2. `.env` du script

```bash
cd /opt/glpi-oracle-sync
cat > .env << 'EOF'
# ORACLE
ORACLE_HOST=localhost
ORACLE_PORT=1521
ORACLE_SERVICE=XEPDB1
ORACLE_USER=EMP_APP
ORACLE_PASSWORD=EmpAppPwd1!
# GLPI
GLPI_API_URL=http://82.165.217.9:8080/apirest.php
#GLPI_APP_TOKEN=TON_APP_TOKEN
GLPI_APP_TOKEN=3vuDiA6NsvCW6qp9uRUr9ZuieuoJNukZ8aCerWu5
#GLPI_USER_TOKEN=TON_USER_TOKEN
GLPI_USER_TOKEN=6LbzOJZ0kgod7dqgQQGLvBxDbM1iJra7z9A3Orum

# Sync
SYNC_DRY_RUN=false
EOF
```

### 6.3. `field_mapping.json`

> ⚠️ Note : on mappe `phone_number` (Oracle) → `phone` (GLPI)

```bash
cat > field_mapping.json << 'EOF'
{
  "key_glpi_field": "registration_number",

  "fields": {
    "employee_code": ["name", "registration_number"],
    "first_name": "firstname",
    "last_name": "realname",
    "email": "email",
    "phone_number": "phone"
  }
}
EOF
```

### 6.4. Dépendances Python

En user `glpi` :

```bash
pip3 install --user oracledb python-dotenv requests glpi-api
```

* * *

7\. Script Python `oracle_to_glpi_sync.py` (version actuelle)
-------------------------------------------------------------

Fichier : `/opt/glpi-oracle-sync/oracle_to_glpi_sync.py`

Je te le redonne intégralement avec les corrections (`phone_number`, gestion erreurs, auth GLPI) :

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Synchronisation Oracle -> GLPI (utilisateurs)
- Lecture des employés Oracle
- Mapping vers les Users GLPI via l'API REST
- Création / Mise à jour / Désactivation
- Logging propre + mapping Oracle/GLPI externalisé (field_mapping.json)
"""

import os
import sys
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Union

import oracledb
import glpi_api
from glpi_api import GLPIError
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 0. Constantes chemin
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ENV = BASE_DIR / ".env"
DEFAULT_MAPPING_FILE = BASE_DIR / "field_mapping.json"
DEFAULT_LOG_FILE = BASE_DIR / "sync.log"

# ---------------------------------------------------------------------------
# 1. Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("oracle_to_glpi_sync")
    logger.setLevel(level)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console (optionnel, pratique pour les tests manuels)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger

# ---------------------------------------------------------------------------
# 2. Dataclasses & mapping externe
# ---------------------------------------------------------------------------

@dataclass
class Employee:
    employee_code: str
    first_name: str
    last_name: str
    email: str
    phone_number: str
    department: str
    job_title: str
    status: str  # ex: ACTIVE / INACTIVE

@dataclass
class MappingConfig:
    key_glpi_field: str
    field_map: Dict[str, Union[str, List[str]]]

def load_mapping(mapping_file: Path, logger: logging.Logger) -> MappingConfig:
    if not mapping_file.exists():
        logger.error("Fichier de mapping introuvable: %s", mapping_file)
        raise SystemExit(1)

    with mapping_file.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    key_glpi_field = raw.get("key_glpi_field", "registration_number")
    field_map = raw.get("fields", {})

    if not isinstance(field_map, dict) or not field_map:
        logger.error("Mapping 'fields' invalide dans %s", mapping_file)
        raise SystemExit(1)

    logger.info(
        "Mapping chargé: key_glpi_field=%s, champs Oracle=%s",
        key_glpi_field,
        ", ".join(field_map.keys()),
    )
    return MappingConfig(key_glpi_field=key_glpi_field, field_map=field_map)

# ---------------------------------------------------------------------------
# 3. Config .env
# ---------------------------------------------------------------------------

def load_config(logger: logging.Logger) -> Dict[str, Any]:
    env_path = Path(os.getenv("SYNC_ENV_FILE", DEFAULT_ENV))
    load_dotenv(dotenv_path=env_path)

    glpi_url = os.getenv("GLPI_API_URL") or os.getenv("GLPI_URL")
    dry_raw = os.getenv("SYNC_DRY_RUN", os.getenv("GLPI_DRY_RUN", "false"))

    cfg = {
        # Oracle
        "oracle_host": os.getenv("ORACLE_HOST", "localhost"),
        "oracle_port": int(os.getenv("ORACLE_PORT", "1521")),
        "oracle_service": os.getenv("ORACLE_SERVICE", "XEPDB1"),
        "oracle_user": os.getenv("ORACLE_USER"),
        "oracle_password": os.getenv("ORACLE_PASSWORD"),

        # GLPI
        "glpi_url": glpi_url,
        "glpi_app_token": os.getenv("GLPI_APP_TOKEN"),
        "glpi_user_token": os.getenv("GLPI_USER_TOKEN"),
        "glpi_entities_id": int(os.getenv("GLPI_ENTITIES_ID", "0")),
        "dry_run": dry_raw.lower() in ("1", "true", "yes"),
    }

    missing = [k for k, v in cfg.items() if v in (None, "")]
    if missing:
        logger.error("Variables manquantes dans .env: %s", ", ".join(missing))
        raise SystemExit(1)

    return cfg

# ---------------------------------------------------------------------------
# 4. Oracle
# ---------------------------------------------------------------------------

EMPLOYEE_QUERY = """
SELECT
    employee_code,
    first_name,
    last_name,
    email,
    phone_number,
    department,
    job_title,
    status
FROM employees
"""

def get_oracle_connection(cfg: Dict[str, Any], logger: logging.Logger):
    logger.info(
        "Connexion Oracle host=%s port=%s service=%s",
        cfg["oracle_host"],
        cfg["oracle_port"],
        cfg["oracle_service"],
    )
    dsn = oracledb.makedsn(
        cfg["oracle_host"],
        cfg["oracle_port"],
        service_name=cfg["oracle_service"],
    )
    return oracledb.connect(
        user=cfg["oracle_user"],
        password=cfg["oracle_password"],
        dsn=dsn,
    )

def fetch_employees(conn, logger: logging.Logger) -> Dict[str, Employee]:
    logger.info("Exécution requête Oracle: %s", EMPLOYEE_QUERY.strip())
    cur = conn.cursor()
    cur.execute(EMPLOYEE_QUERY)

    employees: Dict[str, Employee] = {}

    for row in cur:
        emp = Employee(
            employee_code=str(row[0]).strip(),
            first_name=(row[1] or "").strip(),
            last_name=(row[2] or "").strip(),
            email=(row[3] or "").strip(),
            phone_number=(row[4] or "").strip(),
            department=(row[5] or "").strip(),
            job_title=(row[6] or "").strip(),
            status=(row[7] or "").strip(),
        )
        if not emp.employee_code:
            logger.warning(
               "Ligne Oracle ignorée: employee_code vide (row=%s)", row
            )
            continue

        employees[emp.employee_code] = emp

    cur.close()
    logger.info("Oracle → %d employés récupérés", len(employees))
    return employees

# ---------------------------------------------------------------------------
# 5. GLPI
# ---------------------------------------------------------------------------

def get_glpi_client(cfg: Dict[str, Any], logger: logging.Logger) -> glpi_api.GLPI:
    logger.info("Connexion à GLPI via API: %s", cfg["glpi_url"])
    # glpi_api.GLPI(url, apptoken, auth, ...)
    glpi = glpi_api.GLPI(
        url=cfg["glpi_url"],
        apptoken=cfg["glpi_app_token"],
        auth=cfg["glpi_user_token"],
    )
    logger.info("Session GLPI initialisée avec succès")
    return glpi

def load_glpi_users(
    glpi: glpi_api.GLPI,
    key_glpi_field: str,
    logger: logging.Logger,
) -> Dict[str, Dict[str, Any]]:
    logger.info("Récupération des utilisateurs GLPI via API (User)")
    users = glpi.get_all_items("User") or []

    by_code: Dict[str, Dict[str, Any]] = {}
    duplicated_codes = set()

    for item in users:
        code = str(item.get(key_glpi_field) or "").strip()
        if not code:
            continue
        if code in by_code:
            logger.warning(
                "GLPI: %s=%s dupliqué (ids %s, %s)",
                key_glpi_field,
                code,
                by_code[code].get("id"),
                item.get("id"),
            )
            duplicated_codes.add(code)
            continue
        by_code[code] = item

    logger.info(
        "GLPI → %d utilisateurs dont %d avec %s renseigné",
        len(users),
        len(by_code),
        key_glpi_field,
    )
    if duplicated_codes:
        logger.warning(
            "Clés GLPI dupliquées sur %s: %s",
            key_glpi_field,
            ", ".join(sorted(duplicated_codes)),
        )

    return by_code

def build_comment(emp: Employee) -> str:
    return f"Synchro Oracle - Dept={emp.department}, Job={emp.job_title}"

def employee_to_glpi_payload(
    emp: Employee,
    mapping: MappingConfig,
    entities_id: int,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}

    for oracle_attr, glpi_fields in mapping.field_map.items():
        value = getattr(emp, oracle_attr, None)
        if isinstance(glpi_fields, str):
            payload[glpi_fields] = value
        else:
            for f in glpi_fields:
                payload[f] = value

    payload.setdefault("name", emp.employee_code)
    payload.setdefault("registration_number", emp.employee_code)

    payload["is_active"] = 1 if (emp.status or "").upper() == "ACTIVE" else 0
    payload["entities_id"] = entities_id
    payload["comment"] = build_comment(emp)

    return payload

def compute_changes(
    emp: Employee,
    mapping: MappingConfig,
    glpi_user: Dict[str, Any],
    entities_id: int,
) -> Dict[str, Any]:
    changes: Dict[str, Any] = {}

    for oracle_attr, glpi_fields in mapping.field_map.items():
        value = getattr(emp, oracle_attr, None)

        if isinstance(glpi_fields, str):
            old = glpi_user.get(glpi_fields)
            if (old or "") != (value or ""):
                changes[glpi_fields] = value
        else:
            for f in glpi_fields:
                old = glpi_user.get(f)
                if (old or "") != (value or ""):
                    changes[f] = value

    new_active = 1 if (emp.status or "").upper() == "ACTIVE" else 0
    old_active = int(glpi_user.get("is_active", 1))
    if old_active != new_active:
        changes["is_active"] = new_active

    new_comment = build_comment(emp)
    if (glpi_user.get("comment") or "") != new_comment:
        changes["comment"] = new_comment

    if changes:
        changes["entities_id"] = entities_id
        changes["id"] = glpi_user["id"]

    return changes

# ---------------------------------------------------------------------------
# 6. Boucle principale de synchro
# ---------------------------------------------------------------------------

def sync_oracle_to_glpi(
    cfg: Dict[str, Any],
    mapping: MappingConfig,
    logger: logging.Logger,
) -> None:
    dry = cfg["dry_run"]
    logger.info("===== DÉBUT SYNCHRO Oracle → GLPI (dry_run=%s) =====", dry)

    stats = {
        "created": 0,
        "updated": 0,
        "disabled": 0,
        "errors": 0,
        "skipped": 0,
    }

    try:
        try:
            conn = get_oracle_connection(cfg, logger)
        except oracledb.DatabaseError as e:
            logger.error("Connexion Oracle échouée: %s", e)
            stats["errors"] += 1
            return

        with conn:
            try:
                employees = fetch_employees(conn, logger)
            except oracledb.DatabaseError as e:
                logger.error("Erreur lors de la récupération des employés Oracle: %s", e)
                stats["errors"] += 1
                return

        glpi = get_glpi_client(cfg, logger)
        try:
            glpi_users = load_glpi_users(glpi, mapping.key_glpi_field, logger)

            for code, emp in employees.items():
                glpi_user = glpi_users.get(code)

                if glpi_user is None:
                    payload = employee_to_glpi_payload(
                        emp, mapping, cfg["glpi_entities_id"]
                    )
                    logger.info("[CREATE] User code=%s payload=%s", code, payload)

                    if dry:
                        stats["created"] += 1
                        continue

                    try:
                        res = glpi.add("User", payload)
                        logger.info("[CREATE] Réponse GLPI pour %s: %s", code, res)
                        stats["created"] += 1
                    except GLPIError as e:
                        stats["errors"] += 1
                        logger.error("Échec création GLPI pour %s: %s", code, e)
                    continue

                changes = compute_changes(
                    emp, mapping, glpi_user, cfg["glpi_entities_id"]
                )
                if not changes:
                    stats["skipped"] += 1
                    continue

                logger.info("[UPDATE] EMP%s: changements=%s", code, changes)

                if dry:
                    stats["updated"] += 1
                    continue

                try:
                    res = glpi.update("User", changes)
                    logger.info("[UPDATE/DISABLE] GLPI response for %s: %s", code, res)
                    stats["updated"] += 1
                except GLPIError as e:
                    stats["errors"] += 1
                    logger.error("Échec update GLPI pour %s: %s", code, e)

        finally:
            try:
                glpi.kill_session()
                logger.info("Session GLPI terminée")
            except Exception as e:
                logger.warning("Erreur lors du kill_session GLPI: %s", e)

    finally:
        logger.info("===== RÉSUMÉ SYNCHRO =====")
        logger.info("Créés      : %d", stats["created"])
        logger.info("Modifiés   : %d", stats["updated"])
        logger.info("Désactivés : %d", stats["disabled"])
        logger.info("Ignorés    : %d", stats["skipped"])
        logger.info("Erreurs    : %d", stats["errors"])
        logger.info("Dry run    : %s", dry)
        logger.info("===== FIN SYNCHRO Oracle → GLPI =====")

# ---------------------------------------------------------------------------
# 7. Point d'entrée
# ---------------------------------------------------------------------------

def main():
    log_path = Path(os.getenv("SYNC_LOG_FILE", DEFAULT_LOG_FILE))
    logger = setup_logging(log_path)

    try:
        mapping_file = Path(os.getenv("SYNC_MAPPING_FILE", DEFAULT_MAPPING_FILE))
        mapping = load_mapping(mapping_file, logger)
        cfg = load_config(logger)
        sync_oracle_to_glpi(cfg, mapping, logger)
    except Exception as e:
        logger.error("[ERROR] Exception non gérée: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
```

* * *

8\. Utilisation & cron
----------------------

### 8.1. Test manuel

```bash
cd /opt/glpi-oracle-sync
> sync.log   # pour repartir sur un log propre si tu veux
python3 oracle_to_glpi_sync.py
cat sync.log
```

Tu dois voir :

*   connexion Oracle OK,
*   connexion GLPI OK,
*   nombre d’employés récupérés,
*   des `[CREATE]` / `[UPDATE]` ou `Ignorés`.

### 8.2. Cron toutes les 5 minutes

En user `glpi` :

```bash
crontab -e
```

Ajouter :

```cron
*/5 * * * * cd /opt/glpi-oracle-sync && /usr/bin/python3 oracle_to_glpi_sync.py
```

Pour recharger, rien à faire de plus : `crond` lit automatiquement la crontab modifiée.

* * *

9\. Gestion des logs & supervision
----------------------------------

*   Fichier log : `/opt/glpi-oracle-sync/sync.log`
*   Format : `YYYY-MM-DD HH:MM:SS [LEVEL] message`

### 9.1. Rotation simple via logrotate

Créer `/etc/logrotate.d/glpi-oracle-sync` (en root) :

```text
/opt/glpi-oracle-sync/sync.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
```

### 9.2. Supervision Zabbix (idée)

*   Créer un item Zabbix qui lit `sync.log` (log item ou trapper).
*   Définir un trigger sur :
    *   présence de `[ERROR]`,
    *   ou absence de ligne récente (pas d’exécution depuis X minutes).

* * *

2\. Mise en Git / GitHub depuis la VPS
======================================

On va :

1.  Initialiser un repo Git dans `/opt/glpi-oracle-sync`.
2.  Créer un repo sur GitHub.
3.  Lier les deux et pousser le code.

2.1. Préparer Git sur la VPS
----------------------------

En user `glpi` :

```bash
su - glpi
cd /opt/glpi-oracle-sync
```

Configurer ton identité (si pas déjà fait) :

```bash
git config --global user.name "Ton Nom"
git config --global user.email "ton.email@example.com"
```

Initialiser le repo :

```bash
git init
```

Créer un `.gitignore` minimal :

```bash
cat > .gitignore << 'EOF'
# Fichiers sensibles / locaux
.env
sync.log

# Python
__pycache__/
*.pyc

# Divers
*.swp
EOF
```

Créer un petit `README.md` :

```bash
cat > README.md << 'EOF'
# Oracle → GLPI Sync

Script de synchronisation des employés depuis une base Oracle XE vers GLPI
via l'API REST.

- Script principal : `oracle_to_glpi_sync.py`
- Configuration : `.env`, `field_mapping.json`
EOF
```

Ajouter les fichiers :

```bash
git add oracle_to_glpi_sync.py field_mapping.json .gitignore README.md
git status
```

Commit initial :

```bash
git commit -m "Initial commit: Oracle to GLPI sync script"
```

* * *

2.2. Créer le repo sur GitHub
-----------------------------

Sur le site GitHub (dans ton navigateur) :

1.  Bouton **New repository**.
2.  Nom par ex. : `glpi-oracle-sync`.
3.  Public ou private selon ton besoin.
4.  Tu peux **laisser vide** (pas besoin de README/LICENCE initiaux, sinon il faudra gérer un merge/pull au premier push).
5.  Valider.

GitHub va t’afficher l’URL du repo, par ex :

*   HTTPS : `https://github.com/TonUser/glpi-oracle-sync.git`
*   ou SSH : `git@github.com:TonUser/glpi-oracle-sync.git`

* * *

2.3. Choix de l’authentification GitHub
---------------------------------------

### Option A – HTTPS + Personal Access Token (simple)

Sur GitHub :

*   Settings → Developer settings → Personal access tokens → Tokens (classic)
*   Créer un **PAT** avec scopes minimal `repo`.

Sur la VPS, on utilisera ce PAT comme “mot de passe” lors du `git push`.

* * *

2.4. Lier le repo local au repo GitHub
--------------------------------------

Dans `/opt/glpi-oracle-sync` :

```bash
cd /opt/glpi-oracle-sync
git branch -M main
git remote add origin https://github.com/TonUser/glpi-oracle-sync.git
```

(Adapte `TonUser` et le nom du repo.)

* * *

2.5. Premier push
-----------------

```bash
git push -u origin main
```

Git va te demander :

*   **Username** : ton login GitHub,
*   **Password** : ton **Personal Access Token** (PAT), pas ton mot de passe normal.

Après succès :

*   Le code est sur GitHub,
*   Les prochains `git push` ne nécessitent plus `-u`,
*   Tu peux collaborer / versionner tranquillement.

* * *

2.6. Workflow typique de modification
-------------------------------------

Quand tu modifies le script :

```bash
cd /opt/glpi-oracle-sync
git status
git diff oracle_to_glpi_sync.py

git add oracle_to_glpi_sync.py
git commit -m "Handle GLPI auth via user token"
git push
```

Tu peux ensuite :

*   Taguer des versions,
*   Ajouter d’autres fichiers (doc, scripts SQL),
*   Faire des branches pour des variantes (ex. support d’autres sources que Oracle
