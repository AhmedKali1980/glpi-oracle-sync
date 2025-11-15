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
    # .env explicite ou défaut
    env_path = Path(os.getenv("SYNC_ENV_FILE", DEFAULT_ENV))
    load_dotenv(dotenv_path=env_path)

    # Supporte GLPI_API_URL (préféré) ou GLPI_URL (fallback)
    glpi_url = os.getenv("GLPI_API_URL") or os.getenv("GLPI_URL")

    # Supporte SYNC_DRY_RUN (préféré) ou GLPI_DRY_RUN (fallback)
    dry_raw = os.getenv("SYNC_DRY_RUN", os.getenv("GLPI_DRY_RUN", "false"))

    cfg = {
        # Oracle
        "oracle_host": os.getenv("ORACLE_HOST", "localhost"),
        "oracle_port": int(os.getenv("ORACLE_PORT", "1521")),
        "oracle_service": os.getenv("ORACLE_SERVICE", "XEPDB1"),
        "oracle_user": os.getenv("ORACLE_USER"),
        "oracle_password": os.getenv("ORACLE_PASSWORD"),

        # GLPI
        "glpi_url": glpi_url,  # ex: http://IP:8080/apirest.php
        "glpi_app_token": os.getenv("GLPI_APP_TOKEN"),
        "glpi_user_token": os.getenv("GLPI_USER_TOKEN"),
        "glpi_entities_id": int(os.getenv("GLPI_ENTITIES_ID", "0")),
        "dry_run": dry_raw.lower() in ("1", "true", "yes"),
    }

    missing = [k for k, v in cfg.items() if v in (None, "")]
    if missing:
        logger.error(
            "Variables manquantes dans .env: %s",
            ", ".join(missing),
        )
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
    # Authentification directe via user token (paramètre `auth`)
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

    # Mapping générique fields Oracle -> champs GLPI
    for oracle_attr, glpi_fields in mapping.field_map.items():
        value = getattr(emp, oracle_attr, None)
        if isinstance(glpi_fields, str):
            payload[glpi_fields] = value
        else:
            for f in glpi_fields:
                payload[f] = value

    # Valeurs "logiques" supplémentaires
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

    # 1) Champs mappés
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

    # 2) is_active (status)
    new_active = 1 if (emp.status or "").upper() == "ACTIVE" else 0
    old_active = int(glpi_user.get("is_active", 1))
    if old_active != new_active:
        changes["is_active"] = new_active

    # 3) comment
    new_comment = build_comment(emp)
    if (glpi_user.get("comment") or "") != new_comment:
        changes["comment"] = new_comment

    # 4) entities_id + id (obligatoires pour update)
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
    logger.info(
        "===== DÉBUT SYNCHRO Oracle → GLPI (dry_run=%s) =====",
        dry,
    )

    stats = {
        "created": 0,
        "updated": 0,
        "disabled": 0,
        "errors": 0,
        "skipped": 0,
    }

    try:
        # Connexions
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
                logger.error(
                    "Erreur lors de la récupération des employés Oracle: %s", e
                )
                stats["errors"] += 1
                return

        glpi = get_glpi_client(cfg, logger)
        try:
            glpi_users = load_glpi_users(glpi, mapping.key_glpi_field, logger)

            # Boucle de synchro
            for code, emp in employees.items():
                glpi_user = glpi_users.get(code)

                # --- CAS 1 : utilisateur absent → création ---
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
                        logger.info(
                            "[CREATE] Réponse GLPI pour %s: %s", code, res
                        )
                        stats["created"] += 1
                    except GLPIError as e:
                        stats["errors"] += 1
                        logger.error(
                            "Échec création GLPI pour %s: %s", code, e
                        )
                        # pas de stacktrace, mais on continue
                    continue

                # --- CAS 2 : utilisateur existant ---
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
                    logger.info(
                        "[UPDATE/DISABLE] GLPI response for %s: %s", code, res
                    )
                    # on considère que ce sont des updates (ou disable) réussis
                    stats["updated"] += 1
                except GLPIError as e:
                    stats["errors"] += 1
                    logger.error(
                        "Échec update GLPI pour %s: %s", code, e
                    )

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
        # Dernier filet de sécurité: on log proprement, sans stack trace
        logger.error("[ERROR] Exception non gérée: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()


