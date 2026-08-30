import os
import re
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

try:
    from compose_hardening_contract import validate_compose_hardening_contract
except ModuleNotFoundError:
    from infra.tests.compose_hardening_contract import (
        validate_compose_hardening_contract,
    )


class DuplicateKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that fails closed on duplicate mapping keys."""


def construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


DuplicateKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


def load_yaml_document(source: str) -> dict:
    document = yaml.load(source, Loader=DuplicateKeySafeLoader)
    assert isinstance(document, dict)
    return document


def load_compose_yaml(source: str) -> dict:
    document = load_yaml_document(source)
    assert isinstance(document.get("services"), dict)
    return document


root = Path(__file__).resolve().parents[2]
nginx_source = (root / "infra/nginx/nginx.conf").read_text(encoding="utf-8")
nginx_log_start = nginx_source.index("log_format connectmd_json")
nginx_log_end = nginx_source.index("access_log", nginx_log_start)
nginx_log = nginx_source[nginx_log_start:nginx_log_end]
assert "log_format connectmd_json escape=json" in nginx_log
assert '"path":"$uri"' in nginx_log
assert '"method":"$request_method"' in nginx_log
assert '"status":$status' in nginx_log
assert '"request_id":"$request_id"' in nginx_log
assert "$request_uri" not in nginx_log
assert "$args" not in nginx_log
assert "$query_string" not in nginx_log
assert not re.search(r"\$request(?:[^A-Za-z_]|$)", nginx_log)
for forbidden_variable in (
    "$remote_addr",
    "$http_user_agent",
    "$http_authorization",
    "$http_cookie",
    "$request_body",
    "$request_body_file",
):
    assert forbidden_variable not in nginx_log
required_observability_fields = {
    "$request_time": '"request_time":$request_time',
    "$upstream_response_time": '"upstream_response_time":"$upstream_response_time"',
    "$upstream_status": '"upstream_status":"$upstream_status"',
}
for variable, field in required_observability_fields.items():
    assert nginx_log.count(variable) == 1
    assert field in nginx_log
deploy = (root / "infra/scripts/deploy.sh").read_text(encoding="utf-8")
restore = (root / "infra/scripts/restore.sh").read_text(encoding="utf-8")
health = (root / "infra/scripts/health.sh").read_text(encoding="utf-8")
compose = (root / "compose.yaml").read_text(encoding="utf-8")
compose_production = (root / "compose.prod.yaml").read_text(encoding="utf-8")
environment_example = (root / ".env.example").read_text(encoding="utf-8")
library = (root / "infra/scripts/lib.sh").read_text(encoding="utf-8")
database_role_contract = (root / "infra/postgres/database-role-contract.sql").read_text(
    encoding="utf-8"
)
rollback = (root / "infra/scripts/rollback.sh").read_text(encoding="utf-8")
update = (root / "infra/scripts/update.sh").read_text(encoding="utf-8")
tls = (root / "infra/scripts/tls.sh").read_text(encoding="utf-8")
rebuild = (root / "infra/scripts/rebuild-search.sh").read_text(encoding="utf-8")
rebuild_taxonomy = (root / "infra/scripts/rebuild-taxonomy.sh").read_text(
    encoding="utf-8"
)
retention = (root / "infra/scripts/retention.sh").read_text(encoding="utf-8")
backup = (root / "infra/scripts/backup.sh").read_text(encoding="utf-8")
reconfigure = (root / "infra/scripts/reconfigure.sh").read_text(encoding="utf-8")
release_accept = (root / "infra/scripts/release-accept.sh").read_text(encoding="utf-8")
journal_init = (root / "infra/scripts/init-deletion-journal.sh").read_text(
    encoding="utf-8"
)
cli_source = (root / "apps/api/app/cli.py").read_text(encoding="utf-8")
lifecycle_worker_source = (root / "apps/api/app/account_erasure_worker.py").read_text(
    encoding="utf-8"
)
deletion_journal_source = (
    root / "apps/api/app/services/deletion_journal.py"
).read_text(encoding="utf-8")
continuous_integration = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
gitignore_path = root / ".gitignore"
gitignore_rules = {
    line.strip()
    for line in gitignore_path.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}
dockerignore_path = root / ".dockerignore"
dockerignore_rules = {
    line.strip()
    for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}
for repository_build_exclusion in (
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "**/__pycache__",
):
    assert repository_build_exclusion in dockerignore_rules
https_smoke = (root / "infra/tests/https-smoke.sh").read_text(encoding="utf-8")
acceptance_state_contract = (
    root / "infra/tests/acceptance-state-contract.sh"
).read_text(encoding="utf-8")
recovery_roundtrip = (root / "infra/tests/recovery-roundtrip.sh").read_text(
    encoding="utf-8"
)
exact_search_env_contract = (
    root / "infra/tests/exact-search-env-contract.sh"
).read_text(encoding="utf-8")
environment_example_contract = (
    root / "infra/tests/environment-example-contract.sh"
).read_text(encoding="utf-8")
journal_image_contract = (
    root / "infra/tests/init-deletion-journal-image-contract.sh"
).read_text(encoding="utf-8")
hostname_contract = (root / "infra/tests/hostname-contract.sh").read_text(
    encoding="utf-8"
)
proxy_trust_contract = (root / "infra/tests/proxy-trust-contract.py").read_text(
    encoding="utf-8"
)
api_dockerfile = (root / "apps/api/Dockerfile").read_text(encoding="utf-8")
frontend_dockerignore_path = root / "apps/web/.dockerignore"
assert frontend_dockerignore_path.is_file()
frontend_dockerignore_rules = {
    line.strip()
    for line in frontend_dockerignore_path.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}
for frontend_dockerignore_rule in (
    ".env",
    ".env.*",
    "!.env.example",
    "node_modules",
    ".next",
    "coverage",
    ".cache",
    ".eslintcache",
    ".nyc_output",
    ".turbo",
    ".vitest",
    "test-results",
    "playwright-report",
    "tsconfig.tsbuildinfo",
    "*.log",
    "npm-debug.log*",
    "yarn-debug.log*",
    "yarn-error.log*",
    "pnpm-debug.log*",
):
    assert frontend_dockerignore_rule in frontend_dockerignore_rules
for frontend_build_input in (
    "package.json",
    "package-lock.json",
    "Dockerfile",
    "next.config.ts",
    "postcss.config.mjs",
    "tailwind.config.ts",
    "tsconfig.json",
    "next-env.d.ts",
    "app",
    "components",
    "lib",
    "public",
    "scripts",
):
    assert frontend_build_input not in frontend_dockerignore_rules
deployment_guide = (root / "docs/deployment.md").read_text(encoding="utf-8")

continuous_integration_yaml = load_yaml_document(continuous_integration)
continuous_integration_jobs = continuous_integration_yaml.get("jobs")
assert isinstance(continuous_integration_jobs, dict)
api_continuous_integration_job = continuous_integration_jobs.get("api")
assert isinstance(api_continuous_integration_job, dict)
api_continuous_integration_steps = api_continuous_integration_job.get("steps")
assert isinstance(api_continuous_integration_steps, list)
all_contract_invocations = []
checkout_steps = []
for job_name, job in continuous_integration_jobs.items():
    assert isinstance(job, dict), job_name
    steps = job.get("steps", [])
    assert isinstance(steps, list), job_name
    for step_index, step in enumerate(steps):
        if isinstance(step, dict):
            uses = step.get("uses")
            if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                checkout_steps.append((job_name, step_index, step))
        if isinstance(step, dict) and "operational-contracts.py" in step.get("run", ""):
            all_contract_invocations.append((job_name, step_index, step["run"]))
assert len(checkout_steps) == 5
for job_name, step_index, checkout_step in checkout_steps:
    checkout_with = checkout_step.get("with")
    assert isinstance(checkout_with, dict), (
        f"CI checkout step {job_name}[{step_index}] must have a mapping-valued with block"
    )
    assert checkout_with.get("persist-credentials") is False, (
        f"CI checkout step {job_name}[{step_index}] must disable credential persistence"
    )
assert len(all_contract_invocations) == 1
contract_job_name, contract_step_index, contract_invocation = all_contract_invocations[
    0
]
assert contract_job_name == "api"
assert contract_invocation == "python ../../infra/tests/operational-contracts.py"
api_requirement_install_index = next(
    index
    for index, step in enumerate(api_continuous_integration_steps)
    if step == {
        "run": "python -m pip install --require-hashes -r requirements-test.lock"
    }
)
assert api_requirement_install_index < contract_step_index

compose_function = library[
    library.index("compose() {") : library.index("read_env_value()")
]
assert 'project_name="${CONNECTMD_COMPOSE_PROJECT_NAME:-}"' in compose_function
assert (
    "CONNECTMD_COMPOSE_PROJECT_NAME must be lowercase and valid for Docker Compose"
    in compose_function
)
assert "^[a-z0-9][a-z0-9_-]{0,62}$" in compose_function
assert 'project_args=(--project-name "$project_name")' in compose_function
assert "require_secure_env_file" in compose_function
assert compose_function.index("require_secure_env_file") < compose_function.index(
    "docker compose"
)
assert "assert_env_file_matches_process_environment" in compose_function
assert compose_function.index("require_secure_env_file") < compose_function.index(
    "assert_env_file_matches_process_environment"
) < compose_function.index("docker compose")
assert 'docker compose "${project_args[@]}" "${env_args[@]}"' in compose_function

secure_env_guard = library[
    library.index("require_secure_env_file() {") : library.index("ensure_repo() {")
]
assert '[ ! -L "$ENV_FILE" ] || die ".env must not be a symlink"' in secure_env_guard
assert (
    '[ "$file_type" = "regular file" ] || die ".env must be a regular file"'
    in secure_env_guard
)
assert "stat -c '%u' -- \"$ENV_FILE\"" in secure_env_guard
assert ".env must be owned by the effective deploy account" in secure_env_guard
assert 'if [ "$(uname -s)" = "Linux" ]; then' in secure_env_guard
assert (
    '[ "$file_mode" = "600" ] || die ".env permissions must be exactly 600 on Linux"'
    in secure_env_guard
)
ensure_repo_function = library[
    library.index("ensure_repo() {") : library.index("ensure_clean_source() {")
]
assert ensure_repo_function.index(
    "require_secure_env_file"
) < ensure_repo_function.index("docker compose version")

ensure_clean_source_function = library[
    library.index("ensure_clean_source() {") : library.index(
        "acquire_operation_lock() {"
    )
]
assert ".connectmd-staged-release.env" in gitignore_rules
assert (
    'status="$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=normal)"'
    in ensure_clean_source_function
)
assert (
    '[ -z "$status" ] || die "Refusing release: tracked, staged, or untracked source changes are present"'
    in ensure_clean_source_function
)
assert release_accept.index("ensure_clean_source") < release_accept.index(
    'if [ ! -e "$STAGED_RELEASE_FILE" ]'
)
assert rollback.index("ensure_clean_source") < rollback.index(
    'if [ -e "$STAGED_RELEASE_FILE" ]'
)
git_binary = shutil.which("git")
assert git_binary is not None
staged_ignore_probe = subprocess.run(
    [
        git_binary,
        "-C",
        str(root),
        "check-ignore",
        "--quiet",
        "--no-index",
        ".connectmd-staged-release.env",
    ],
    check=False,
)
assert staged_ignore_probe.returncode == 0
unrelated_source_ignore_probe = subprocess.run(
    [
        git_binary,
        "-C",
        str(root),
        "check-ignore",
        "--quiet",
        "--no-index",
        "connectmd-unrelated-source-probe.txt",
    ],
    check=False,
)
assert unrelated_source_ignore_probe.returncode == 1

operation_lock = library[
    library.index("acquire_operation_lock() {") : library.index("compose() {")
]
assert (
    'if [ "${CONNECTMD_OPERATION_LOCK_HELD:-}" = "1" ] && [ -e "/proc/$$/fd/9" ]; then'
    in operation_lock
)
assert (
    'inherited_lock="$(readlink -f "/proc/$$/fd/9" 2>/dev/null || true)"'
    in operation_lock
)
assert (
    'flock -n 9 || die "Inherited connect.md operation lock is not held"'
    in operation_lock
)
assert (
    '[ ! -L "$lock_file" ] || die "Connect.md operation lock path must not be a symlink"'
    in operation_lock
)
assert 'exec 9>>"$lock_file"' in operation_lock
assert "Connect.md operation lock must be a regular single-link file" in operation_lock
assert (
    "Connect.md operation lock descriptor must be a regular single-link file"
    in operation_lock
)
assert (
    "Connect.md operation lock descriptor no longer matches the lock path"
    in operation_lock
)

assert retention.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail\n")
retention_repo = retention.index("ensure_repo")
retention_lock = retention.index("acquire_operation_lock")
retention_environment = retention.index("validate_production_env")
retention_active = retention.index("load_active_release_identity")
retention_pin = retention.index('export CONNECTMD_IMAGE_TAG="$RELEASE_IMAGE_TAG"')
retention_identity = retention.index(
    'assert_service_image_identity api "$RELEASE_API_IMAGE_ID"'
)
retention_running = retention.index("service_is_running api")
retention_exec = retention.index(
    "compose exec -T api python -m app.cli retention run --limit 100"
)
assert (
    retention_repo
    < retention_lock
    < retention_environment
    < retention_active
    < retention_pin
    < retention_identity
    < retention_running
    < retention_exec
)
for forbidden_retention_action in (
    "compose run",
    "compose up",
    "load_staged_release",
    "STAGED_",
    "curl ",
    "wget ",
    "http://",
    "https://",
):
    assert forbidden_retention_action not in retention
assert (
    "23 * * * * cd /srv/connectmd/app && bash infra/scripts/retention.sh "
    ">> /srv/connectmd/retention.log 2>&1"
    in deployment_guide
)
assert "fresh, dedicated connect.md VPS only" in deployment_guide
assert "missing or failed hourly run is an operational retention failure" in deployment_guide

backup_root_function = library[
    library.index("backup_root() {") : library.index("backup_directory() {")
]
assert (
    '[ "$root" != / ] || die "CONNECTMD_BACKUP_DIR must not be the filesystem root"'
    in backup_root_function
)
assert 'case "$REPO_ROOT" in' in backup_root_function
assert (
    "CONNECTMD_BACKUP_DIR must be a dedicated directory and cannot contain the repository"
    in backup_root_function
)
assert (
    '[ ! -L "$configured_root" ] || die "CONNECTMD_BACKUP_DIR must not be a symlink"'
    in backup_root_function
)
assert 'root="$(realpath -m "$configured_root")"' in backup_root_function
assert '(umask 077 && mkdir -p -- "$root")' in backup_root_function
assert (
    '[ -d "$root" ] || die "CONNECTMD_BACKUP_DIR must be a directory"'
    in backup_root_function
)
assert (
    "CONNECTMD_BACKUP_DIR must be owned by the effective deploy account"
    in backup_root_function
)
assert (
    "CONNECTMD_BACKUP_DIR permissions must be exactly 700 on Linux"
    in backup_root_function
)

bash_binary = os.environ.get("BASH") or shutil.which("bash")
if bash_binary is not None:
    bash_supports_contracts = (
        subprocess.run(
            [
                bash_binary,
                "-c",
                "command -v realpath >/dev/null && command -v flock >/dev/null && command -v stat >/dev/null",
            ],
            check=False,
        ).returncode
        == 0
    )
    if bash_supports_contracts:
        with tempfile.TemporaryDirectory() as temporary_directory:
            isolated_root = Path(temporary_directory) / "repository"
            isolated_library = isolated_root / "infra/scripts/lib.sh"
            isolated_library.parent.mkdir(parents=True)
            shutil.copy2(root / "infra/scripts/lib.sh", isolated_library)

            if os.name == "posix" and os.uname().sysname == "Linux":
                environment_file = isolated_root / ".env"
                environment_file.write_text("CONNECTMD_TEST=1\n", encoding="utf-8")
                environment_file.chmod(0o600)
                valid_environment = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; require_secure_env_file',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert valid_environment.returncode == 0, valid_environment.stderr

                environment_file.chmod(0o644)
                broad_environment = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; require_secure_env_file',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert broad_environment.returncode != 0
                assert (
                    ".env permissions must be exactly 600 on Linux"
                    in broad_environment.stderr
                )

                environment_file.unlink()
                environment_target = isolated_root / "environment-target"
                environment_target.write_text("CONNECTMD_TEST=1\n", encoding="utf-8")
                environment_target.chmod(0o600)
                environment_file.symlink_to(environment_target)
                symlink_environment = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; require_secure_env_file',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert symlink_environment.returncode != 0
                assert ".env must not be a symlink" in symlink_environment.stderr
                environment_file.unlink()
                environment_file.write_text("CONNECTMD_TEST=1\n", encoding="utf-8")
                environment_file.chmod(0o600)

                id_directory = Path(temporary_directory) / "id-wrapper"
                id_directory.mkdir()
                real_id = shutil.which("id")
                assert real_id is not None
                id_wrapper = id_directory / "id"
                id_wrapper.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -eu\n"
                    'if [ "${1:-}" = "-u" ]; then printf "%s\\n" 4294967294; exit 0; fi\n'
                    'exec "$CONNECTMD_TEST_REAL_ID" "$@"\n',
                    encoding="utf-8",
                )
                id_wrapper.chmod(0o700)
                foreign_owner_environment = dict(os.environ)
                foreign_owner_environment.update(
                    {
                        "PATH": f"{id_directory}{os.pathsep}{foreign_owner_environment['PATH']}",
                        "CONNECTMD_TEST_REAL_ID": real_id,
                    }
                )
                foreign_environment = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; require_secure_env_file',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=foreign_owner_environment,
                )
                assert foreign_environment.returncode != 0
                assert (
                    ".env must be owned by the effective deploy account"
                    in foreign_environment.stderr
                )

                created_backup_root = isolated_root / "new-backup-root"
                missing_backup_root = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; CONNECTMD_BACKUP_DIR="$2"; backup_root',
                        "bash",
                        str(isolated_library),
                        str(created_backup_root),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert missing_backup_root.returncode == 0, missing_backup_root.stderr
                assert created_backup_root.is_dir()
                assert created_backup_root.stat().st_mode & 0o777 == 0o700

                broad_backup_root = isolated_root / "broad-backup-root"
                broad_backup_root.mkdir()
                broad_backup_root.chmod(0o755)
                broad_backup = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; CONNECTMD_BACKUP_DIR="$2"; backup_root',
                        "bash",
                        str(isolated_library),
                        str(broad_backup_root),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert broad_backup.returncode != 0
                assert (
                    "CONNECTMD_BACKUP_DIR permissions must be exactly 700 on Linux"
                    in broad_backup.stderr
                )

                backup_target = isolated_root / "backup-target"
                backup_target.mkdir()
                backup_target.chmod(0o700)
                symlink_backup_root = isolated_root / "symlink-backup-root"
                symlink_backup_root.symlink_to(backup_target, target_is_directory=True)
                symlink_backup = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; CONNECTMD_BACKUP_DIR="$2"; backup_root',
                        "bash",
                        str(isolated_library),
                        str(symlink_backup_root),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert symlink_backup.returncode != 0
                assert (
                    "CONNECTMD_BACKUP_DIR must not be a symlink"
                    in symlink_backup.stderr
                )

                foreign_backup_root = isolated_root / "foreign-backup-root"
                foreign_backup_root.mkdir()
                foreign_backup_root.chmod(0o700)
                foreign_backup = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; CONNECTMD_BACKUP_DIR="$2"; backup_root',
                        "bash",
                        str(isolated_library),
                        str(foreign_backup_root),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=foreign_owner_environment,
                )
                assert foreign_backup.returncode != 0
                assert (
                    "CONNECTMD_BACKUP_DIR must be owned by the effective deploy account"
                    in foreign_backup.stderr
                )

            root_backup = subprocess.run(
                [
                    bash_binary,
                    "-c",
                    'source "$1"; CONNECTMD_BACKUP_DIR=/; backup_root',
                    "bash",
                    str(isolated_library),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert root_backup.returncode != 0
            assert (
                "CONNECTMD_BACKUP_DIR must not be the filesystem root"
                in root_backup.stderr
            )

            holder_environment = dict(os.environ)
            holder_environment.pop("CONNECTMD_OPERATION_LOCK_HELD", None)
            lock_holder = subprocess.Popen(
                [
                    bash_binary,
                    "-c",
                    'source "$1"; acquire_operation_lock; printf "locked\\n"; IFS= read -r _',
                    "bash",
                    str(isolated_library),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=holder_environment,
            )
            try:
                assert lock_holder.stdout is not None
                holder_stdout = lock_holder.stdout.readline()
                if holder_stdout != "locked\n":
                    holder_stderr = (
                        lock_holder.stderr.read()
                        if lock_holder.poll() is not None
                        and lock_holder.stderr is not None
                        else ""
                    )
                    raise AssertionError(
                        "operation lock holder did not announce its lock: "
                        f"stdout={holder_stdout!r}, stderr={holder_stderr!r}"
                    )
                contender_environment = dict(os.environ)
                contender_environment.pop("CONNECTMD_OPERATION_LOCK_HELD", None)
                lock_contender = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; acquire_operation_lock',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=contender_environment,
                )
                assert lock_contender.returncode != 0
                assert (
                    "Another connect.md operational workflow is already running"
                    in lock_contender.stderr
                )
            finally:
                if lock_holder.poll() is None:
                    assert lock_holder.stdin is not None
                    lock_holder.stdin.write("\n")
                    lock_holder.stdin.close()
                lock_holder.wait(timeout=5)

            if os.name == "posix":
                lock_target = isolated_root / "operation-lock-target"
                lock_target_contents = "operation-lock-target-must-not-change\n"
                lock_target.write_text(lock_target_contents, encoding="utf-8")
                operation_lock_path = isolated_root / ".connectmd-operations.lock"
                operation_lock_path.unlink()
                operation_lock_path.symlink_to(lock_target)
                symlink_lock = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; acquire_operation_lock',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert symlink_lock.returncode != 0
                assert (
                    "Connect.md operation lock path must not be a symlink"
                    in symlink_lock.stderr
                )
                assert operation_lock_path.is_symlink()
                assert lock_target.read_text(encoding="utf-8") == lock_target_contents

                operation_lock_path.unlink()
                os.link(lock_target, operation_lock_path)
                hard_link_lock = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; acquire_operation_lock',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert hard_link_lock.returncode != 0
                assert (
                    "Connect.md operation lock must be a regular single-link file"
                    in hard_link_lock.stderr
                )
                assert lock_target.read_text(encoding="utf-8") == lock_target_contents

                operation_lock_path.unlink()
                operation_lock_path.write_text("initial lock bytes\n", encoding="utf-8")
                stat_directory = Path(temporary_directory) / "stat-wrapper"
                stat_directory.mkdir()
                real_stat = shutil.which("stat")
                assert real_stat is not None
                replacement_target = isolated_root / "replacement-target"
                replacement_contents = "replacement target must not change\n"
                replacement_target.write_text(replacement_contents, encoding="utf-8")
                stat_wrapper = stat_directory / "stat"
                stat_wrapper.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -eu\n"
                    'if [ "${!#}" = "$CONNECTMD_TEST_OPERATION_LOCK_PATH" ]; then\n'
                    '  rm -f -- "$CONNECTMD_TEST_OPERATION_LOCK_PATH"\n'
                    '  ln -s -- "$CONNECTMD_TEST_OPERATION_LOCK_TARGET" "$CONNECTMD_TEST_OPERATION_LOCK_PATH"\n'
                    "fi\n"
                    'exec "$CONNECTMD_TEST_REAL_STAT" "$@"\n',
                    encoding="utf-8",
                )
                stat_wrapper.chmod(0o700)
                replacement_environment = dict(os.environ)
                replacement_environment.update(
                    {
                        "PATH": f"{stat_directory}{os.pathsep}{replacement_environment['PATH']}",
                        "CONNECTMD_TEST_OPERATION_LOCK_PATH": str(operation_lock_path),
                        "CONNECTMD_TEST_OPERATION_LOCK_TARGET": str(replacement_target),
                        "CONNECTMD_TEST_REAL_STAT": real_stat,
                    }
                )
                replaced_lock = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'source "$1"; acquire_operation_lock',
                        "bash",
                        str(isolated_library),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=replacement_environment,
                )
                assert replaced_lock.returncode != 0
                assert (
                    "Connect.md operation lock path must not be a symlink"
                    in replaced_lock.stderr
                )
                assert operation_lock_path.is_symlink()
                assert (
                    replacement_target.read_text(encoding="utf-8")
                    == replacement_contents
                )

                operation_lock_path.unlink()
                inherited_contents = "inherited lock bytes must not change\n"
                operation_lock_path.write_text(inherited_contents, encoding="utf-8")
                inherited_lock = subprocess.run(
                    [
                        bash_binary,
                        "-c",
                        'exec 9>>"$2"; flock -n 9; CONNECTMD_OPERATION_LOCK_HELD=1; source "$1"; acquire_operation_lock; printf "inherited\\n"',
                        "bash",
                        str(isolated_library),
                        str(operation_lock_path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert inherited_lock.returncode == 0, inherited_lock.stderr
                assert inherited_lock.stdout == "inherited\n"
                assert (
                    operation_lock_path.read_text(encoding="utf-8")
                    == inherited_contents
                )

deploy_stop = deploy.index(
    "compose --profile account-lifecycle stop account-erasure-worker search-projection-worker nginx frontend api >/dev/null"
)
deploy_bootstrap_roles = deploy.index("bootstrap_database_roles")
deploy_migrate = deploy.index("db-migrate alembic upgrade head")
deploy_role = deploy.index("reconcile_database_roles")
deploy_taxonomy_backfill = deploy.index(
    "taxonomy-admin python -m app.cli taxonomy backfill --if-required"
)
deploy_taxonomy_verify = deploy.index(
    "taxonomy-admin python -m app.cli taxonomy verify"
)
deploy_exact_backfill = deploy.index(
    "exact-search-admin python -m app.cli exact-search backfill --if-required"
)
deploy_exact_verify = deploy.index(
    "exact-search-admin python -m app.cli exact-search verify"
)
deploy_rebuild = deploy.index(
    "compose --profile search-operations run --rm --no-deps -T search-admin python -m app.cli rebuild-search"
)
deploy_stage = deploy.index(
    'write_staged_release "$source_revision" "$image_tag" "$api_image_id" "$web_image_id" "$nginx_image_id"'
)
deploy_validate = deploy.index("validate_production_env")
deploy_pepper_guard = deploy.index("assert_api_key_pepper_unchanged")
deploy_build = deploy.index(
    'build_or_reuse_release_images "$image_tag" "$source_revision"'
)
deploy_start = deploy.index(
    "compose up -d --no-build converter search-projection-worker api frontend nginx"
)
deploy_lifecycle_wait = deploy.index(
    "wait_for_profiled_service account-lifecycle account-erasure-worker"
)
deploy_complete = deploy.index("rollout_complete=true")
assert (
    deploy_stop
    < deploy_migrate
    < deploy_role
    < deploy_taxonomy_backfill
    < deploy_taxonomy_verify
    < deploy_exact_backfill
    < deploy_exact_verify
    < deploy_rebuild
    < deploy_start
    < deploy_lifecycle_wait
    < deploy_stage
    < deploy_complete
)
assert deploy_start < deploy_lifecycle_wait < deploy_stage < deploy_complete
assert deploy_validate < deploy_pepper_guard < deploy_build < deploy_stage
assert deploy.index('assert_exact_search_image_contract "$image_tag"') < deploy_stop
assert 'source_revision="$(current_source_revision)"' in deploy
assert "connectmd-restore-state-v2" in deploy
assert "connectmd-restore-state-v3" in deploy
for restored_identity_field in (
    "restore_backup_format",
    "restore_backup_acceptance_digest",
    "restore_source_revision",
    "restore_api_image_id",
    "restore_web_image_id",
    "restore_nginx_image_id",
    "restore_release_receipt_digest",
    "restore_api_state",
    "restore_converter_state",
    "restore_projection_state",
    "restore_worker_state",
    "restore_frontend_state",
    "restore_nginx_state",
):
    assert restored_identity_field in deploy
restore_state_identity_preflight = deploy.index(
    'assert_release_images_match "$restore_image_tag" "$restore_api_image_id" "$restore_web_image_id" "$restore_nginx_image_id"'
)
assert restore_state_identity_preflight < deploy_build < deploy_stop
assert deploy.index('assert_service_image_identity api "$api_image_id"') > deploy_start
assert (
    deploy.index('assert_service_image_identity frontend "$web_image_id"')
    > deploy_start
)
assert (
    deploy.index('assert_service_image_identity nginx "$nginx_image_id"') > deploy_start
)
assert deploy_stage < deploy_complete
restore_validate = restore.index("validate_production_env")
restore_destructive_boundary = restore.index(
    "write_restore_state in_progress unavailable"
)
assert restore_validate < restore_destructive_boundary
assert (
    'if [ -f "$RELEASE_ENV_FILE" ]; then\n  assert_api_key_pepper_unchanged\nfi'
    in deploy
)
assert "|| true" not in deploy[deploy_stop:deploy_migrate]
assert "profiled_service_state account-lifecycle account-erasure-worker" in deploy
assert "stop_failed_rollout_on_exit" in deploy
assert "wait_for_profiled_service account-lifecycle account-erasure-worker" in deploy
assert "wait_for_service search-projection-worker" in deploy
assert "phase=complete" in deploy
assert "Completed restore receipt does not match durable restore state" in deploy
assert "clear_matching_completed_restore_state" in release_accept
assert "search_rebuild_pending" in deploy
assert deploy.index("search_rebuild_pending=false") < deploy_start
deploy_restored_topology = deploy.index(
    'if [ "$restore_state_present" = true ] && [ "$restore_state_format" = "connectmd-restore-state-v3" ]; then',
    deploy.index("start_restored_service()"),
)
restored_start_order = [
    deploy.index('start_restored_service converter "$restore_converter_state"'),
    deploy.index(
        'start_restored_service search-projection-worker "$restore_projection_state"'
    ),
    deploy.index('start_restored_service api "$restore_api_state"'),
    deploy.index('start_restored_service frontend "$restore_frontend_state"'),
    deploy.index('start_restored_service nginx "$restore_nginx_state"'),
]
assert deploy_rebuild < deploy_restored_topology < restored_start_order[0]
assert restored_start_order == sorted(restored_start_order)
assert restored_start_order[-1] < deploy_stage
restored_service_helper = deploy[
    deploy.index("start_restored_service()") : deploy_restored_topology
]
assert restored_service_helper.index(
    'compose up -d --no-build --no-deps "$service"'
) < restored_service_helper.index('wait_for_service "$service"')
assert restored_service_helper.index('wait_for_service "$service"') < restored_service_helper.index(
    'assert_service_image_identity "$service" "$expected_identity"'
)
assert 'case "$prior_state" in' in restored_service_helper
assert "absent | stopped" in restored_service_helper
assert 'service_is_active "$service"' in restored_service_helper
deploy_journal_live = deploy.index("deletion-journal verify-live")
assert deploy_stop < deploy_bootstrap_roles < deploy_migrate < deploy_role < deploy_journal_live < deploy_rebuild
assert deploy_migrate < deploy_exact_backfill < deploy_exact_verify < deploy_rebuild

restore_edge_stop = restore.index("compose stop nginx frontend")
restore_stop = restore.index(
    "compose --profile account-lifecycle stop account-erasure-worker search-projection-worker api converter"
)
restore_image = restore.index(
    'assert_release_images_match "$backup_image_tag" "$backup_api_image_id" "$backup_web_image_id" "$backup_nginx_image_id"'
)
restore_release_receipt_validation = restore.index(
    'validate_release_receipt "$backup_release_receipt" "$backup_source_revision" "$backup_image_tag" "$backup_api_image_id" "$backup_web_image_id" "$backup_nginx_image_id"'
)
restore_release_receipt_digest = restore.index(
    "Backup release receipt does not match the recorded generation"
)
restore_acceptance_receipt_validation = restore.index(
    "load_release_acceptance", restore_release_receipt_digest
)
restore_acceptance_receipt_digest = restore.index(
    "Backup acceptance receipt does not match the recorded generation",
    restore_acceptance_receipt_validation,
)
restore_checkpoint_image_binding = restore.index(
    'CONNECTMD_IMAGE_TAG="$backup_image_tag"', restore_image
)
restore_journal_checkpoint = restore.index("deletion-journal verify-checkpoint")
restore_authority_probe = restore.index("DELETION_AUTHORITY_CONTRACT_VERSION")
restore_exact_image_contract = restore.index("assert_exact_search_image_contract")
restore_receipt_preflight = restore.index(
    "verify_registration_receipt",
    restore.index('receipt="$registration_root/$generation_id.env"'),
)
restore_state_write = restore.index(
    "write_restore_state in_progress unavailable", restore_stop
)
restore_mutation = restore.index("mutation_started=true")
restore_archive_validation = restore.index("-m app.services.backup_archive")
restore_role_bootstrap = restore.index("bootstrap_database_roles")
restore_migrator_attestation = restore.index("attest_restore_migrator_role")
restore_database = restore.index("database-restore")
restore_role_reconcile = restore.index("reconcile_database_roles")
restore_register = restore.index("python -m app.cli account-backup register")
restore_complete = restore.index("restore_complete=true")
assert (
    restore_receipt_preflight
    < restore_release_receipt_validation
    < restore_release_receipt_digest
    < restore_acceptance_receipt_validation
    < restore_acceptance_receipt_digest
    < restore_image
    < restore_checkpoint_image_binding
    < restore_journal_checkpoint
    < restore_archive_validation
    < restore_authority_probe
    < restore_exact_image_contract
    < restore_edge_stop
    < restore_stop
    < restore_state_write
    < restore_mutation
    < restore_role_bootstrap
    < restore_migrator_attestation
    < restore_database
    < restore_role_reconcile
    < restore_register
    < restore_complete
)
assert restore_state_write < restore_mutation
assert restore_archive_validation < restore_mutation
archive_validation = restore[
    restore.rfind("docker run", 0, restore_archive_validation) : restore.index(
        "pg_restore --list", restore_archive_validation
    )
]
assert '--network none --read-only' in archive_validation
assert '-v "$directory/markdown-storage.tar.gz:/restore/markdown-storage.tar.gz:ro"' in archive_validation
assert '"$backup_api_image_id"' in archive_validation
assert restore_database < restore_register < restore_complete
assert "ensure_clean_source" in restore
verify_backup_start = library.index("verify_backup() {")
verify_backup_end = library.index("\n}\n", verify_backup_start)
verify_backup_contract = library[verify_backup_start:verify_backup_end]
assert "metadata.env postgres.dump markdown-storage.tar.gz SHA256SUMS" in verify_backup_contract
assert '[ ! -L "$directory/$backup_artifact" ]' in verify_backup_contract
restore_source_preflight = restore.index(
    '[ "$(current_source_revision)" = "$backup_source_revision" ]'
)
assert restore_source_preflight < restore_image < restore_stop
assert "connectmd-backup-v2" in library
assert "connectmd-restore-state-v3" in restore
assert "connectmd-restore-state-v2" in deploy
assert "connectmd-restore-state-v2" in library
assert "connectmd-restore-state-v3" in library
assert "backup_format" in restore
assert "backup_acceptance_receipt_digest" in restore
for backup_identity_field in (
    "backup_source_revision",
    "backup_api_image_id",
    "backup_web_image_id",
    "backup_nginx_image_id",
    "backup_release_receipt_digest",
):
    assert backup_identity_field in restore
assert restore.index('api_prior_state="$(service_state api)"') < restore_stop
for prior_state_capture in (
    'converter_prior_state="$(service_state converter)"',
    'projection_prior_state="$(service_state search-projection-worker)"',
    'worker_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"',
    'frontend_prior_state="$(service_state frontend)"',
    'nginx_prior_state="$(service_state nginx)"',
):
    assert restore.index(prior_state_capture) < restore_edge_stop
assert (
    restore.index('api_prior_tag="$(managed_api_tag "$(service_image api)")"')
    < restore_stop
)
assert (
    restore.index('projection_prior_state="$(service_state search-projection-worker)"')
    < restore_stop
)
assert (
    restore.index(
        'projection_prior_tag="$(managed_api_tag "$(service_image search-projection-worker)")"'
    )
    < restore_stop
)
assert (
    restore.index(
        'worker_prior_tag="$(managed_api_tag "$(profiled_service_image account-lifecycle account-erasure-worker)")"'
    )
    < restore_stop
)
for prior_identity_marker in (
    'api_prior_image_id="$(service_image_identity api)"',
    'converter_prior_image_id="$(service_image_identity converter)"',
    'projection_prior_image_id="$(service_image_identity search-projection-worker)"',
    'worker_prior_image_id="$(profiled_service_image_identity account-lifecycle account-erasure-worker)"',
    'frontend_prior_image_id="$(service_image_identity frontend)"',
    'nginx_prior_image_id="$(service_image_identity nginx)"',
):
    assert prior_identity_marker in restore
restore_one_shot_preflight = restore.index("for profiled_consumer in")
restore_state_capture = restore.index('api_prior_state="$(service_state api)"')
assert restore_one_shot_preflight < restore_state_capture < restore_edge_stop < restore_stop
assert 'state_is_serving "$nginx_prior_state"' in restore
assert "Serving Nginx requires running API and frontend before destructive restore" in restore
for one_shot_consumer in (
    "search-operations:search-admin",
    "taxonomy-operations:taxonomy-admin",
    "exact-search-operations:exact-search-admin",
    "ops:storage-backup",
    "ops:storage-restore",
    "ops:backup-verify",
):
    assert one_shot_consumer in restore[restore_one_shot_preflight:restore_state_capture]
assert "One-shot canonical-state consumer must finish before destructive restore" in restore
restore_edge_verify = restore.index(
    "if service_is_active nginx || service_is_active frontend"
)
restore_writer_verify = restore.index(
    "if service_is_active api || service_is_active search-projection-worker || profiled_service_is_active account-lifecycle account-erasure-worker"
)
restore_one_shot_recheck = restore.index(
    "assert_no_active_one_shot_consumers", restore_writer_verify
)
assert "service_is_active converter" in restore[restore_writer_verify:restore_one_shot_recheck]
assert restore.count("assert_no_active_one_shot_consumers") == 3
assert (
    restore_edge_stop
    < restore_edge_verify
    < restore_stop
    < restore_writer_verify
    < restore_one_shot_recheck
    < restore_state_write
)
restore_failure_restart = restore[
    restore.index("restart_services_on_failure() {") : restore.index(
        "trap restart_services_on_failure EXIT"
    )
]
restore_unprofiled_recovery = restore[
    restore.index("restore_unprofiled_service_before_mutation()") : restore.index(
        "restore_lifecycle_worker_before_mutation()"
    )
]
assert restore_unprofiled_recovery.index(
    'assert_image_identity "$image" "$tag" "$identity"'
) < restore_unprofiled_recovery.index(
    'compose up -d --no-build --no-deps "$service"'
)
assert restore_unprofiled_recovery.index(
    'compose up -d --no-build --no-deps "$service"'
) < restore_unprofiled_recovery.index('wait_for_service "$service" 30')
assert restore_unprofiled_recovery.index('wait_for_service "$service" 30') < restore_unprofiled_recovery.index(
    'assert_service_image_identity "$service" "$identity"'
)
recovery_order = [
    restore_failure_restart.index("restore_unprofiled_service_before_mutation converter"),
    restore_failure_restart.index(
        "restore_unprofiled_service_before_mutation search-projection-worker"
    ),
    restore_failure_restart.index("restore_unprofiled_service_before_mutation api"),
    restore_failure_restart.index("restore_lifecycle_worker_before_mutation"),
    restore_failure_restart.index("restore_unprofiled_service_before_mutation frontend"),
    restore_failure_restart.index("restore_unprofiled_service_before_mutation nginx"),
]
assert recovery_order == sorted(recovery_order)
assert "trap - EXIT" in restore_failure_restart
assert "Pre-mutation restore failure could not re-establish every prior service state" in restore_failure_restart
assert (
    "Nginx, frontend, API, converter, search-projection-worker, and account-erasure-worker remain stopped"
    in restore
)
assert (
    restore.index('export CONNECTMD_IMAGE_TAG="$backup_image_tag"') > restore_database
)
assert restore.index('created_epoch="$(date -u -d "$created_at" +%s)"') < restore_stop
assert restore.index('evidence_probe="$(mktemp') < restore_stop
assert "profiled_service_is_active account-lifecycle account-erasure-worker" in restore
assert "service_is_active search-projection-worker" in restore
assert 'receipt="$registration_root/$generation_id.env"' in restore
assert "RESTORE_STATE_FILE" in restore
assert restore.index("write_restore_state in_progress unavailable") < restore_mutation
assert (
    restore.index('write_restore_state complete "$registration_receipt_digest"')
    < restore_complete
)
assert 'mv -f -- "$temporary" "$RESTORE_STATE_FILE"' in restore
assert "search_rebuild_pending=true" in restore
for durable_prior_state in (
    "api_prior_state",
    "converter_prior_state",
    "projection_prior_state",
    "worker_prior_state",
    "frontend_prior_state",
    "nginx_prior_state",
):
    assert f"printf '{durable_prior_state}=%s\\n'" in restore
    assert durable_prior_state in deploy
    assert durable_prior_state in library
restore_journal_live = restore.index("deletion-journal verify-live")
assert (
    restore_image
    < restore_checkpoint_image_binding
    < restore_journal_checkpoint
    < restore_stop
)
assert restore_database < restore_journal_live < restore_complete
assert "deletion_journal_head_sequence" in restore
assert "deletion_journal_head_digest" in restore
assert (
    "Destructive restore requires an existing durable registration receipt" in restore
)
assert "temporary_receipt" not in restore
assert restore.count("verify_registration_receipt") >= 3
assert "DELETION_AUTHORITY_CONTRACT_VERSION = 1" in deletion_journal_source

assert health.index("validate_production_env") < health.index("wait_for_service")
assert "search-projection-worker" in health
assert "wait_for_profiled_service account-lifecycle account-erasure-worker" in health
assert "readonly service_health_attempts=30" in health
assert "readonly lifecycle_health_attempts=30" in health
assert 'wait_for_service "$service" "$service_health_attempts"' in health
assert 'wait_for_service "$service" 1' not in health
assert (
    'wait_for_profiled_service account-lifecycle account-erasure-worker "$lifecycle_health_attempts"'
    in health
)
assert "SERVICE_HEALTH=%s:PASS" in health
assert "NGINX_INTERNAL_PROBE=PASS" in health
assert "API_READINESS_PROBE=PASS" in health
assert library.index('while [ "$attempts" -gt 0 ]; do', library.index("wait_for_service()")) < library.index(
    "sleep 2", library.index("wait_for_service()")
)
assert '  diagnose_search_projection_worker\n  die "Timed out waiting for $service to become healthy"' in library
assert 'if [ "${lifecycle_enabled:-false}" = "true" ]; then' in health
assert health.index("lifecycle_enabled=") < health.index(
    "wait_for_profiled_service account-lifecycle account-erasure-worker"
)

search_index = "CONNECTMD_MEILISEARCH_INDEX: ${CONNECTMD_MEILISEARCH_INDEX:-documents}"
assert compose.count(search_index) == 5
exact_keyring = "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING: ${CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING:?Set CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING in .env}"
exact_ttl = "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS: ${CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS:-900}"
assert compose.count(exact_keyring) == 3
assert compose.count(exact_ttl) == 3
assert compose.count("create_host_path: false") == 6

worker_start = compose.index("  search-projection-worker:")
worker_end = compose.index("  search-admin:", worker_start)
worker = compose[worker_start:worker_end]
assert "markdown_storage:/app/storage:ro" in worker
assert "read_only: true" in worker
assert "- connectmd_data" in worker
assert "connectmd_app:" not in worker
assert "connectmd_search_projection:" in worker
assert "CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD" in worker
assert "CONNECTMD_SEARCH_PROJECTION_MEILI_KEY" in worker
for forbidden in (
    "CONNECTMD_CLERK_",
    "CONNECTMD_API_KEY_PEPPER",
    "CONNECTMD_ACCOUNT_LIFECYCLE",
    "POSTGRES_PASSWORD",
    "MEILI_MASTER_KEY",
):
    assert forbidden not in worker

api_start = compose.index("  api:")
api_end = compose.index("  converter:", api_start)
api = compose[api_start:api_end]
assert "connectmd_api:" in api
assert "CONNECTMD_API_DB_PASSWORD" in api
assert "POSTGRES_PASSWORD" not in api
assert "CONNECTMD_MEILISEARCH_SEARCH_KEY" in api
assert "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING" in api
assert "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS" in api
assert "CONNECTMD_MAX_UPLOAD_BYTES: ${CONNECTMD_MAX_UPLOAD_BYTES:-10485760}" in api
assert (
    "CONNECTMD_AGENT_OUTREACH_DIRECT_PEER_DAILY_LIMIT: "
    "${CONNECTMD_AGENT_OUTREACH_DIRECT_PEER_DAILY_LIMIT:-100}"
) in api
assert "MEILI_MASTER_KEY" not in api
assert "CONNECTMD_DELETION_JOURNAL_PATH: /deletion-journal" in api
assert (
    "source: ${CONNECTMD_BACKUP_DIR:-./backups}/.connectmd-lifecycle/deletion-journal"
    in api
)

for service_name, next_service, role_name, password_key in (
    ("search-admin", "taxonomy-admin", "connectmd_projection_admin", "CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD"),
    ("taxonomy-admin", "exact-search-admin", "connectmd_projection_admin", "CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD"),
    ("exact-search-admin", "search-key-bootstrap", "connectmd_projection_admin", "CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD"),
    ("account-erasure-worker", "frontend", "connectmd_account_erasure", "CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD"),
):
    section = compose[
        compose.index(f"  {service_name}:") : compose.index(f"  {next_service}:", compose.index(f"  {service_name}:"))
    ]
    assert f"{role_name}:" in section
    assert password_key in section
    assert "POSTGRES_PASSWORD" not in section

for service_name, role_name, password_key in (
    ("db-migrate", "connectmd_migrator", "CONNECTMD_MIGRATOR_DB_PASSWORD"),
    ("database-backup", "connectmd_backup", "CONNECTMD_BACKUP_DB_PASSWORD"),
    ("database-restore", "connectmd_migrator", "CONNECTMD_MIGRATOR_DB_PASSWORD"),
):
    section_start = compose.index(f"  {service_name}:")
    next_service = re.search(r"^  [a-zA-Z0-9_-]+:$", compose[section_start + 3 :], re.MULTILINE)
    section_end = (
        section_start + 3 + next_service.start() if next_service is not None else len(compose)
    )
    section = compose[section_start:section_end]
    assert 'profiles: ["database-operations"]' in section
    assert role_name in section
    assert password_key in section
assert "target: /deletion-journal" in api
assert "CONNECTMD_DELETION_WITNESS_PATH: /deletion-head-witness" in api
assert "CONNECTMD_DELETION_WITNESS_HMAC_KEY:" in api
assert (
    "source: ${CONNECTMD_DELETION_WITNESS_DIR:?Set CONNECTMD_DELETION_WITNESS_DIR in .env}"
    in api
)
assert "target: /deletion-head-witness" in api
assert api.count("create_host_path: false") == 2
assert "read_only: true" in api
assert "/tmp:size=64m,mode=1777" in api
assert '"--forwarded-allow-ips", "172.31.254.2"' in api
assert '"--forwarded-allow-ips", "*"' not in api
assert "ports:" not in api and "expose:" not in api

lifecycle_start = compose.index("  account-erasure-worker:")
lifecycle_end = compose.index("  frontend:", lifecycle_start)
lifecycle_worker = compose[lifecycle_start:lifecycle_end]
for setting in (
    "CONNECTMD_ACCOUNT_LIFECYCLE_MAX_HEALTHY_BACKLOG",
    "CONNECTMD_ACCOUNT_LIFECYCLE_MAX_HEALTHY_DEAD_LETTERS",
    "CONNECTMD_ACCOUNT_LIFECYCLE_MAX_HEALTHY_ELIGIBLE_AGE_SECONDS",
    "CONNECTMD_ACCOUNT_LIFECYCLE_HEARTBEAT_PATH",
):
    assert setting in lifecycle_worker
assert "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING" in lifecycle_worker
assert "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS" in lifecycle_worker
assert "/tmp:size=16m,mode=1777" in lifecycle_worker
assert "database_ready" in lifecycle_worker
assert "deletion_journal_ready" in lifecycle_worker
assert "provider_ready" in lifecycle_worker
assert "search_ready" in lifecycle_worker
assert "time.time()-os.path.getmtime(p)<45" in lifecycle_worker
assert "d['state']=='healthy'" in lifecycle_worker
assert "CONNECTMD_DELETION_WITNESS_PATH: /deletion-head-witness" in lifecycle_worker
assert "CONNECTMD_DELETION_WITNESS_HMAC_KEY:" in lifecycle_worker
assert (
    "source: ${CONNECTMD_DELETION_WITNESS_DIR:?Set CONNECTMD_DELETION_WITNESS_DIR in .env}"
    in lifecycle_worker
)
assert lifecycle_worker.count("read_only: true") == 3
assert lifecycle_worker.count("create_host_path: false") == 2

assert "ACCOUNT_LIFECYCLE_HEALTH_CONTRACT_VERSION = 1" in lifecycle_worker_source
assert "_database_health_snapshot" in lifecycle_worker_source
assert "verify_live_deletion_mirror" in lifecycle_worker_source
assert "DeletionCommitmentJournal(settings)" in lifecycle_worker_source
assert "if health_verified:" in lifecycle_worker_source
assert "executor.run_once(limit=1)" in lifecycle_worker_source
assert "await provider.check_ready()" in lifecycle_worker_source
assert "await search.check_ready()" in lifecycle_worker_source
assert "temporary.chmod(0o600)" in lifecycle_worker_source
assert "heartbeat.unlink(missing_ok=True)" in lifecycle_worker_source
for forbidden in ("subject_hmac", "resource_id", "deletion_id", "receipt_hmac"):
    assert (
        f'"{forbidden}"'
        not in lifecycle_worker_source[
            lifecycle_worker_source.index("return {") : lifecycle_worker_source.index(
                "async def _refresh_health_heartbeat"
            )
        ]
    )

admin_start = compose.index("  search-admin:")
admin_end = compose.index("  taxonomy-admin:", admin_start)
admin = compose[admin_start:admin_end]
assert 'profiles: ["search-operations"]' in admin
assert "MEILI_MASTER_KEY" in admin
assert "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING" not in admin
assert "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS" not in admin
assert "CONNECTMD_DELETION_JOURNAL_PATH: /deletion-journal" in admin
assert (
    "source: ${CONNECTMD_BACKUP_DIR:-./backups}/.connectmd-lifecycle/deletion-journal"
    in admin
)
assert "CONNECTMD_DELETION_WITNESS_PATH: /deletion-head-witness" in admin
assert "CONNECTMD_DELETION_WITNESS_HMAC_KEY:" in admin
assert (
    "source: ${CONNECTMD_DELETION_WITNESS_DIR:?Set CONNECTMD_DELETION_WITNESS_DIR in .env}"
    in admin
)
assert admin.count("read_only: true") == 3
assert admin.count("create_host_path: false") == 2

taxonomy_admin_start = compose.index("  taxonomy-admin:")
taxonomy_admin_end = compose.index("  exact-search-admin:", taxonomy_admin_start)
taxonomy_admin = compose[taxonomy_admin_start:taxonomy_admin_end]
assert 'profiles: ["taxonomy-operations"]' in taxonomy_admin
assert 'command: ["python", "-m", "app.cli", "taxonomy", "verify"]' in taxonomy_admin
assert "markdown_storage:/app/storage:ro" in taxonomy_admin
assert "read_only: true" in taxonomy_admin
assert "- connectmd_data" in taxonomy_admin
assert "connectmd_app:" not in taxonomy_admin
assert "connectmd_projection_admin:" in taxonomy_admin
assert "CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD" in taxonomy_admin
assert "POSTGRES_PASSWORD" not in taxonomy_admin
assert "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING" not in taxonomy_admin
assert "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS" not in taxonomy_admin
for forbidden in (
    "CONNECTMD_MEILISEARCH",
    "MEILI_MASTER_KEY",
    "CONNECTMD_CLERK_",
    "CONNECTMD_API_KEY_PEPPER",
    "CONNECTMD_ACCOUNT_LIFECYCLE",
    "CONNECTMD_LIFECYCLE_",
    "CONNECTMD_DELETION_",
    "/deletion-journal",
    "/deletion-head-witness",
):
    assert forbidden not in taxonomy_admin

production_taxonomy_start = compose_production.index("  taxonomy-admin:")
production_taxonomy_end = compose_production.index(
    "  exact-search-admin:", production_taxonomy_start
)
production_taxonomy = compose_production[
    production_taxonomy_start:production_taxonomy_end
]
for resource_limit in ("mem_limit: 256m", 'cpus: "0.50"', "pids_limit: 64"):
    assert resource_limit in production_taxonomy

exact_admin_start = compose.index("  exact-search-admin:")
exact_admin_end = compose.index("  search-key-bootstrap:", exact_admin_start)
exact_admin = compose[exact_admin_start:exact_admin_end]
assert 'profiles: ["exact-search-operations"]' in exact_admin
assert 'command: ["python", "-m", "app.cli", "exact-search", "verify"]' in exact_admin
assert "CONNECTMD_ENVIRONMENT: production" in exact_admin
assert "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING" in exact_admin
assert "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS" in exact_admin
assert "markdown_storage:/app/storage:ro" in exact_admin
assert "- connectmd_data" in exact_admin
assert "connectmd_app:" not in exact_admin
for forbidden in (
    "CONNECTMD_MEILISEARCH",
    "MEILI_MASTER_KEY",
    "CONNECTMD_CLERK_",
    "CONNECTMD_API_KEY_PEPPER",
    "CONNECTMD_ACCOUNT_LIFECYCLE",
    "CONNECTMD_LIFECYCLE_",
    "CONNECTMD_DELETION_",
):
    assert forbidden not in exact_admin

production_exact_start = compose_production.index("  exact-search-admin:")
production_exact_end = compose_production.index(
    "  search-key-bootstrap:", production_exact_start
)
production_exact = compose_production[production_exact_start:production_exact_end]
for resource_limit in ("mem_limit: 256m", 'cpus: "0.50"', "pids_limit: 64"):
    assert resource_limit in production_exact

bootstrap_start = compose.index("  search-key-bootstrap:")
bootstrap_end = compose.index("  account-erasure-worker:", bootstrap_start)
search_key_bootstrap = compose[bootstrap_start:bootstrap_end]
assert 'profiles: ["search-bootstrap"]' in search_key_bootstrap
assert "MEILI_MASTER_KEY" in search_key_bootstrap
assert "- connectmd_data" in search_key_bootstrap
assert "volumes:" not in search_key_bootstrap
for forbidden in (
    "CONNECTMD_DELETION_",
    "CONNECTMD_LIFECYCLE_",
    "CONNECTMD_STORAGE_PATH",
    "POSTGRES",
    "markdown_storage",
):
    assert forbidden not in search_key_bootstrap
production_bootstrap_start = compose_production.index("  search-key-bootstrap:")
production_bootstrap_end = compose_production.index(
    "  account-erasure-worker:", production_bootstrap_start
)
production_bootstrap = compose_production[
    production_bootstrap_start:production_bootstrap_end
]
for resource_limit in ("mem_limit: 128m", 'cpus: "0.25"', "pids_limit: 64"):
    assert resource_limit in production_bootstrap

ABSENT = object()
COMPOSE_API_IMAGE = "connectmd-api:${CONNECTMD_IMAGE_TAG:-local}"
HARDENING_FIELDS = {
    "user",
    "read_only",
    "cap_drop",
    "cap_add",
    "privileged",
    "security_opt",
}
PROTECTED_RUNTIME_OVERRIDE_FIELDS = HARDENING_FIELDS | {
    "build",
    "command",
    "entrypoint",
    "healthcheck",
    "image",
    "networks",
    "network_mode",
    "tmpfs",
    "volumes",
    "volumes_from",
    "devices",
}
API_BUILD_CONTRACT = {"context": ".", "dockerfile": "apps/api/Dockerfile"}

api_healthcheck = {
    "test": [
        "CMD-SHELL",
        "python -c \"import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:8000' + os.environ['API_READINESS_PATH'], timeout=3).read()\"",
    ],
    "interval": "15s",
    "timeout": "5s",
    "retries": 8,
    "start_period": "30s",
}
converter_healthcheck = {
    "test": [
        "CMD-SHELL",
        "python -c \"import os,time; p='/ingest-jobs/.worker-ready'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p)<5\"",
    ],
    "interval": "10s",
    "timeout": "5s",
    "retries": 5,
    "start_period": "10s",
}
search_projection_healthcheck = {
    "test": [
        "CMD-SHELL",
        "python -c \"import json,os,time; p=os.environ['CONNECTMD_SEARCH_PROJECTION_HEARTBEAT_PATH']; d=json.load(open(p,encoding='utf-8')); assert d['state']=='healthy' and time.time()-os.path.getmtime(p)<45\"",
    ],
    "interval": "15s",
    "timeout": "5s",
    "retries": 5,
    "start_period": "20s",
}
lifecycle_healthcheck = {
    "test": [
        "CMD-SHELL",
        "python -c \"import json,os,time; p=os.environ['CONNECTMD_ACCOUNT_LIFECYCLE_HEARTBEAT_PATH']; d=json.load(open(p,encoding='utf-8')); assert d['state']=='healthy' and d['database_ready'] and d['deletion_journal_ready'] and d['provider_ready'] and d['search_ready'] and time.time()-os.path.getmtime(p)<45\"",
    ],
    "interval": "15s",
    "timeout": "5s",
    "retries": 5,
    "start_period": "30s",
}

journal_mount = {
    "type": "bind",
    "source": "${CONNECTMD_BACKUP_DIR:-./backups}/.connectmd-lifecycle/deletion-journal",
    "target": "/deletion-journal",
    "bind": {"create_host_path": False},
}
witness_mount = {
    "type": "bind",
    "source": "${CONNECTMD_DELETION_WITNESS_DIR:?Set CONNECTMD_DELETION_WITNESS_DIR in .env}",
    "target": "/deletion-head-witness",
    "bind": {"create_host_path": False},
}
readonly_journal_mount = {**journal_mount, "read_only": True}
readonly_witness_mount = {**witness_mount, "read_only": True}

PYTHON_SERVICE_RUNTIME_CONTRACTS = {
    "api": {
        "command": [
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--no-access-log",
            "--proxy-headers",
            "--forwarded-allow-ips",
            "172.31.254.2",
        ],
        "healthcheck": api_healthcheck,
        "volumes": [
            "markdown_storage:/app/storage",
            "ingest_jobs:/ingest-jobs",
            journal_mount,
            witness_mount,
        ],
        "tmpfs": ["/tmp:size=64m,mode=1777"],
        "networks": {"connectmd_app": None, "connectmd_data": None},
        "network_mode": ABSENT,
    },
    "converter": {
        "command": ["python", "-m", "app.ingest_worker"],
        "healthcheck": converter_healthcheck,
        "volumes": ["ingest_jobs:/ingest-jobs"],
        "tmpfs": ["/tmp:size=128m,mode=1777"],
        "networks": ABSENT,
        "network_mode": "none",
    },
    "search-projection-worker": {
        "command": ["python", "-m", "app.search_projection_worker", "run"],
        "healthcheck": search_projection_healthcheck,
        "volumes": ["markdown_storage:/app/storage:ro"],
        "tmpfs": ["/tmp:size=16m,mode=1777"],
        "networks": ["connectmd_data"],
        "network_mode": ABSENT,
    },
    "search-admin": {
        "command": ["python", "-m", "app.cli", "rebuild-search"],
        "healthcheck": ABSENT,
        "volumes": [
            "markdown_storage:/app/storage:ro",
            readonly_journal_mount,
            readonly_witness_mount,
        ],
        "tmpfs": ["/tmp:size=16m,mode=1777"],
        "networks": ["connectmd_data"],
        "network_mode": ABSENT,
    },
    "taxonomy-admin": {
        "command": ["python", "-m", "app.cli", "taxonomy", "verify"],
        "healthcheck": ABSENT,
        "volumes": ["markdown_storage:/app/storage:ro"],
        "tmpfs": ["/tmp:size=16m,mode=1777"],
        "networks": ["connectmd_data"],
        "network_mode": ABSENT,
    },
    "exact-search-admin": {
        "command": ["python", "-m", "app.cli", "exact-search", "verify"],
        "healthcheck": ABSENT,
        "volumes": ["markdown_storage:/app/storage:ro"],
        "tmpfs": ["/tmp:size=16m,mode=1777"],
        "networks": ["connectmd_data"],
        "network_mode": ABSENT,
    },
    "search-key-bootstrap": {
        "command": ["python", "-m", "app.search_key_bootstrap"],
        "healthcheck": ABSENT,
        "volumes": ABSENT,
        "tmpfs": ["/tmp:size=16m,mode=1777"],
        "networks": ["connectmd_data"],
        "network_mode": ABSENT,
    },
    "account-erasure-worker": {
        "command": ["python", "-m", "app.account_erasure_worker"],
        "healthcheck": lifecycle_healthcheck,
        "volumes": [
            "markdown_storage:/app/storage",
            readonly_journal_mount,
            readonly_witness_mount,
        ],
        "tmpfs": ["/tmp:size=16m,mode=1777"],
        "networks": ["connectmd_app", "connectmd_data"],
        "network_mode": ABSENT,
    },
}

PUBLIC_SERVICE_PID_LIMITS = {
    "api": 128,
    "frontend": 128,
    "nginx": 64,
}

PUBLIC_SERVICE_HARDENING_CONTRACTS = {
    "frontend": {
        "user": "1001:1001",
        "read_only": True,
        "cap_drop": ["ALL"],
        "cap_add": [],
        "privileged": False,
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:size=16m,mode=1777"],
    },
    # Nginx keeps its root master for 80/443, template generation, and the
    # existing `user nginx` worker transition. These are the only capabilities
    # retained after dropping the default set.
    "nginx": {
        "user": ABSENT,
        "image": "connectmd-nginx:${CONNECTMD_IMAGE_TAG:-local}",
        "build": {"context": "./infra/nginx"},
        "read_only": True,
        "cap_drop": ["ALL"],
        "cap_add": ["NET_BIND_SERVICE", "SETGID", "SETUID"],
        "privileged": False,
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": [
            "/etc/nginx/conf.d:size=1m,mode=0755",
            "/var/cache/nginx:size=16m,mode=0755",
            "/var/run:size=1m,mode=0755",
        ],
        "volumes": [
            "certbot_etc:/etc/letsencrypt:ro",
            "certbot_webroot:/var/www/certbot:ro",
        ],
    },
}


def assert_compose_hardening_contract(
    base_compose: dict, production_compose: dict
) -> None:
    # These images retain their documented operational behavior. The backup
    # helper's fixed UID is pre-existing and required for its read-only volume.
    excluded_base_hardening = {
        "postgres": {},
        "meilisearch": {},
        "storage-backup": {"user": "10001:10001"},
        "storage-restore": {},
        "backup-verify": {},
    }
    validate_compose_hardening_contract(
        base_compose,
        production_compose,
        absent=ABSENT,
        compose_api_image=COMPOSE_API_IMAGE,
        hardening_field_names=HARDENING_FIELDS,
        protected_runtime_override_fields=PROTECTED_RUNTIME_OVERRIDE_FIELDS,
        api_build_contract=API_BUILD_CONTRACT,
        python_service_runtime_contracts=PYTHON_SERVICE_RUNTIME_CONTRACTS,
        public_service_pid_limits=PUBLIC_SERVICE_PID_LIMITS,
        public_service_hardening_contracts=PUBLIC_SERVICE_HARDENING_CONTRACTS,
        excluded_base_hardening=excluded_base_hardening,
    )


base_compose_yaml = load_compose_yaml(compose)
production_compose_yaml = load_compose_yaml(compose_production)
assert_compose_hardening_contract(base_compose_yaml, production_compose_yaml)
base_services = base_compose_yaml["services"]
edge_network = base_compose_yaml["networks"]["connectmd_app"]
assert edge_network["driver"] == "bridge"
assert edge_network["ipam"]["config"] == [
    {"subnet": "172.31.254.0/24", "ip_range": "172.31.254.128/25"}
]
assert base_services["nginx"]["networks"]["connectmd_app"] == {
    "ipv4_address": "172.31.254.2"
}
assert compose.count("${CONNECTMD_CLERK_JWKS_URL:-}") == 2
assert compose.count("${CONNECTMD_CLERK_ISSUER:-}") == 2
assert compose.count("${CONNECTMD_CLERK_AUTHORIZED_PARTIES:-[]}") == 2
assert compose.count("${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:-}") == 2
assert compose.count("${CLERK_SECRET_KEY:-}") == 1
assert "CLERK_JWKS_URL:?" not in compose
assert "CLERK_PUBLISHABLE_KEY:?" not in compose
nginx_environment = base_services["nginx"]["environment"]
assert nginx_environment["CONNECTMD_TLS_MODE"] == "${CONNECTMD_TLS_MODE:-auto}"
assert nginx_environment["CONNECTMD_HTTP_BINDING"].startswith(
    "${CONNECTMD_HTTP_BINDING:-"
)
assert nginx_environment["CONNECTMD_HTTPS_BINDING"].startswith(
    "${CONNECTMD_HTTPS_BINDING:-"
)
assert base_services["nginx"]["labels"] == [
    "traefik.enable=${CONNECTMD_TRAEFIK_ENABLED:-false}",
    "traefik.http.routers.connectmd.rule=Host(`${CONNECTMD_DOMAIN:-connectmd.invalid}`)",
    "traefik.http.routers.connectmd.entrypoints=websecure",
    "traefik.http.routers.connectmd.tls=true",
    "traefik.http.routers.connectmd.tls.certresolver=letsencrypt",
    "traefik.http.services.connectmd.loadbalancer.server.port=80",
]
assert "ports" not in base_services["api"] and "expose" not in base_services["api"]
clerk_backend_environment_keys = {
    "CONNECTMD_CLERK_BACKEND_SECRET",
    "CONNECTMD_CLERK_BACKEND_BASE_URL",
}
services_with_clerk_backend_credentials = {
    service_name
    for service_name, service in base_services.items()
    if clerk_backend_environment_keys & set(service.get("environment", {}))
}
assert services_with_clerk_backend_credentials == {"account-erasure-worker"}
assert clerk_backend_environment_keys <= set(
    base_services["account-erasure-worker"]["environment"]
)
proxy_command = base_services["api"]["command"]
proxy_trust_index = proxy_command.index("--forwarded-allow-ips")
assert proxy_command[proxy_trust_index + 1] == "172.31.254.2"
assert proxy_command[proxy_trust_index + 1] != "*"
assert "/" not in proxy_command[proxy_trust_index + 1]
assert "PROXY_TRUST_CONTRACT=PASS" in proxy_trust_contract
assert "direct request from any untrusted container" in proxy_trust_contract
assert "rightmost" in proxy_trust_contract


def assert_api_readiness_contract(source: str) -> None:
    api_start = source.index("  api:")
    api_end = source.index("  converter:", api_start)
    api = source[api_start:api_end]
    assert "API_READINESS_PATH: /readyz" in api
    assert "API_READINESS_PATH: ${" not in api
    assert "API_HEALTHCHECK_PATH" not in api
    assert "os.environ['API_READINESS_PATH']" in api


assert_api_readiness_contract(compose)
for weakened_compose in (
    compose.replace("API_READINESS_PATH: /readyz", "API_READINESS_PATH: /healthz", 1),
    compose.replace(
        "API_READINESS_PATH: /readyz",
        "API_READINESS_PATH: ${API_READINESS_PATH:-/readyz}",
        1,
    ),
    compose.replace("API_READINESS_PATH: /readyz", "API_HEALTHCHECK_PATH: /healthz", 1),
):
    try:
        assert_api_readiness_contract(weakened_compose)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError("weakened API readiness healthcheck unexpectedly passed")


def assert_hardening_contract_rejects(
    scenario: str, base_compose: dict, production_compose: dict
) -> None:
    try:
        assert_compose_hardening_contract(base_compose, production_compose)
    except AssertionError:
        return
    raise AssertionError(f"hardening contract accepted unsafe {scenario}")


for scenario, mutation in (
    (
        "base entrypoint override",
        lambda service: service.update(entrypoint=["python", "-m", "unsafe"]),
    ),
    ("cap_add", lambda service: service.update(cap_add=["NET_ADMIN"])),
    ("privileged", lambda service: service.update(privileged=True)),
    (
        "relaxed security_opt",
        lambda service: service.update(security_opt=["seccomp:unconfined"]),
    ),
    ("read_only false", lambda service: service.update(read_only=False)),
    ("altered user", lambda service: service.update(user="0:0")),
    (
        "extra read-write volume",
        lambda service: service["volumes"].append("postgres_data:/unexpected"),
    ),
    (
        "altered tmpfs",
        lambda service: service.update(tmpfs=["/tmp:size=128m,mode=1777"]),
    ),
):
    mutated_base = deepcopy(base_compose_yaml)
    mutation(mutated_base["services"]["api"])
    assert_hardening_contract_rejects(scenario, mutated_base, production_compose_yaml)

for service_name, mutations in {
    "frontend": (
        ("frontend non-root user", lambda service: service.update(user="0:0")),
        (
            "frontend cap_add",
            lambda service: service.update(cap_add=["NET_ADMIN"]),
        ),
        ("frontend privileged", lambda service: service.update(privileged=True)),
        ("frontend writable root", lambda service: service.update(read_only=False)),
        (
            "frontend altered tmpfs",
            lambda service: service.update(tmpfs=["/tmp:size=128m,mode=1777"]),
        ),
    ),
    "nginx": (
        ("nginx missing bind capability", lambda service: service.update(cap_add=[])),
        (
            "nginx extra capability",
            lambda service: service.update(cap_add=["SYS_ADMIN"]),
        ),
        ("nginx privileged", lambda service: service.update(privileged=True)),
        ("nginx writable root", lambda service: service.update(read_only=False)),
        ("nginx user override", lambda service: service.update(user="1001:1001")),
        (
            "nginx missing template tmpfs",
            lambda service: service.update(
                tmpfs=["/var/cache/nginx:size=16m,mode=0755"]
            ),
        ),
    ),
}.items():
    for scenario, mutation in mutations:
        mutated_base = deepcopy(base_compose_yaml)
        mutation(mutated_base["services"][service_name])
        assert_hardening_contract_rejects(
            scenario, mutated_base, production_compose_yaml
        )

for service_name, expected_pid_limit in PUBLIC_SERVICE_PID_LIMITS.items():
    mutated_production = deepcopy(production_compose_yaml)
    mutated_production["services"][service_name]["pids_limit"] = expected_pid_limit + 1
    assert_hardening_contract_rejects(
        f"{service_name} production PID limit", base_compose_yaml, mutated_production
    )

for service_name in PUBLIC_SERVICE_HARDENING_CONTRACTS:
    mutated_production = deepcopy(production_compose_yaml)
    mutated_production["services"][service_name]["read_only"] = False
    assert_hardening_contract_rejects(
        f"{service_name} production hardening override",
        base_compose_yaml,
        mutated_production,
    )

mutated_production = deepcopy(production_compose_yaml)
mutated_production["services"]["api"]["read_only"] = False
assert_hardening_contract_rejects(
    "production hardening override", base_compose_yaml, mutated_production
)

for scenario, field, value in (
    ("production entrypoint override", "entrypoint", ["python", "-m", "unsafe"]),
    ("production image override", "image", "connectmd-api:unsafe"),
    (
        "production build override",
        "build",
        {"context": ".", "dockerfile": "unsafe/Dockerfile"},
    ),
):
    mutated_production = deepcopy(production_compose_yaml)
    mutated_production["services"]["api"][field] = value
    assert_hardening_contract_rejects(scenario, base_compose_yaml, mutated_production)

try:
    load_compose_yaml("services:\n  api:\n    read_only: true\n    read_only: false\n")
except ConstructorError:
    pass
else:
    raise AssertionError("duplicate Compose mapping keys must fail closed")
assert (
    "CONNECTMD_MEILISEARCH_API_KEY: ${CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY:-}" in compose
)
assert "CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY=" in environment_example
assert "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING=" in environment_example
assert "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS=900" in environment_example
assert "CONNECTMD_MAX_UPLOAD_BYTES=10485760" in environment_example
assert "CONNECTMD_AGENT_OUTREACH_DIRECT_PEER_DAILY_LIMIT=100" in environment_example
assert "validate_exact_search_cursor_authority" in library
assert "one to three unique kid/secret objects" in library
assert 're.fullmatch(r"[A-Za-z0-9_-]+", secret)' in library
assert "len(decoded) >= 32" in library
assert 'ttl" -ge 60' in library and 'ttl" -le 3600' in library
assert "CONNECTMD_RECRUITING_ENABLED=false" in environment_example
assert "API_READINESS_PATH=/readyz" in environment_example
assert "API_HEALTHCHECK_PATH" not in environment_example
assert "EXACT_SEARCH_ENV_CONTRACT=PASS" in exact_search_env_contract
assert "Counterexample unexpectedly passed" in exact_search_env_contract
assert "standard Base64 plus alphabet" in exact_search_env_contract
assert "standard Base64 slash alphabet" in exact_search_env_contract
assert "exact-search-env-contract.sh" in continuous_integration
assert "proxy-trust-contract.py" in continuous_integration


def assert_exact_search_image_preflight(source: str) -> None:
    start = source.index("assert_exact_search_image_contract() {")
    end = source.index("\n}\n", start)
    contract = source[start:end]
    for marker in (
        "/app/alembic/versions/0025_exact_public_search.py",
        "EXACT_SEARCH_CONTRACT_DIGEST",
        "'exact-search','verify'",
        "args.exact_search_action == 'verify'",
        "'exact-search','backfill','--if-required'",
        "args.exact_search_action == 'backfill'",
        "args.if_required is True",
    ):
        assert marker in contract, marker


assert_exact_search_image_preflight(library)
for missing_capability in (
    "'exact-search','backfill','--if-required'",
    "args.if_required is True",
):
    counterexample = library.replace(missing_capability, "missing", 1)
    try:
        assert_exact_search_image_preflight(counterexample)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"exact-search image preflight accepted missing capability: {missing_capability}"
        )
assert library.index('if [ "$lifecycle_enabled" = true ]; then') < library.index(
    'erasure_meili_key="$(require_secret_value CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY)"'
)


def assert_production_env_contract(source: str) -> None:
    function_start = source.index("validate_production_env() {")
    function_end = source.index("\n}\n", function_start)
    contract = source[function_start:function_end]
    for marker in (
        'api_readiness_path="$(read_env_optional_value API_READINESS_PATH)"',
        'api_readiness_path="${api_readiness_path:-/readyz}"',
        'case "$api_readiness_path" in /readyz) ;; *) die "API_READINESS_PATH must remain /readyz" ;; esac',
        'recruiting_enabled="$(normalize_recruiting_enabled)"',
        'api_base="$(read_env_optional_value NEXT_PUBLIC_API_BASE_URL)"',
        'validate_public_api_base_environment_override "$api_base"',
        'validate_public_api_base "$api_base" "https://$domain"',
        'validate_canonical_https_origin "$site_url" NEXT_PUBLIC_SITE_URL',
        'validate_clerk_authorized_site_origin "$clerk_parties" "$site_url"',
        'clerk_api_configured=false',
        'clerk_frontend_configured=false',
        '[ "$clerk_parties" != "[]" ]',
        'if [ "$clerk_api_configured" = true ]; then',
    ):
        assert marker in contract, marker


assert_production_env_contract(library)
for missing_marker in (
    'case "$api_readiness_path" in /readyz) ;; *) die "API_READINESS_PATH must remain /readyz" ;; esac',
    'recruiting_enabled="$(normalize_recruiting_enabled)"',
    'api_base="$(read_env_optional_value NEXT_PUBLIC_API_BASE_URL)"',
    'validate_public_api_base_environment_override "$api_base"',
    'validate_public_api_base "$api_base" "https://$domain"',
    'validate_canonical_https_origin "$site_url" NEXT_PUBLIC_SITE_URL',
    'validate_clerk_authorized_site_origin "$clerk_parties" "$site_url"',
    '[ "$clerk_parties" != "[]" ]',
):
    counterexample = library.replace(missing_marker, "missing", 1)
    try:
        assert_production_env_contract(counterexample)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"production preflight accepted missing contract: {missing_marker}"
        )


def assert_public_api_base_contract(source: str) -> None:
    function_start = source.index("validate_public_api_base() {")
    function_end = source.index("\n}\n", function_start)
    contract = source[function_start:function_end]
    for marker in (
        "validate_public_api_base() {",
        'case "$api_base" in',
        '"" | "$canonical_origin") ;;',
        "NEXT_PUBLIC_API_BASE_URL must be empty or exactly the canonical CONNECTMD_PUBLIC_BASE_URL HTTPS origin",
    ):
        assert marker in contract, marker


assert_public_api_base_contract(library)
for missing_marker in (
    '"" | "$canonical_origin") ;;',
    "NEXT_PUBLIC_API_BASE_URL must be empty or exactly the canonical CONNECTMD_PUBLIC_BASE_URL HTTPS origin",
):
    weakened = library.replace(missing_marker, "missing", 1)
    try:
        assert_public_api_base_contract(weakened)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"public API base contract accepted missing guard: {missing_marker}"
        )


def assert_canonical_https_origin_contract(source: str) -> None:
    function_start = source.index("validate_canonical_https_origin() {")
    function_end = source.index("\n}\n", function_start)
    contract = source[function_start:function_end]
    for marker in (
        "validate_canonical_https_origin() {",
        'https://*) hostname="${value#https://}" ;;',
        'is_lowercase_dns_hostname "$hostname"',
        "must be a canonical HTTPS origin",
    ):
        assert marker in contract, marker


assert_canonical_https_origin_contract(library)
for missing_marker in (
    'https://*) hostname="${value#https://}" ;;',
    'is_lowercase_dns_hostname "$hostname"',
):
    weakened = library.replace(missing_marker, "missing", 1)
    try:
        assert_canonical_https_origin_contract(weakened)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"canonical HTTPS origin contract accepted missing guard: {missing_marker}"
        )


def assert_public_api_base_override_contract(source: str) -> None:
    function_start = source.index("validate_public_api_base_environment_override() {")
    function_end = source.index("\n}\n", function_start)
    contract = source[function_start:function_end]
    for marker in (
        "validate_public_api_base_environment_override() {",
        'if [[ -v NEXT_PUBLIC_API_BASE_URL && "$NEXT_PUBLIC_API_BASE_URL" != "$configured_base" ]]; then',
        'die "NEXT_PUBLIC_API_BASE_URL environment override must match .env"',
    ):
        assert marker in contract, marker


assert_public_api_base_override_contract(library)
for missing_marker in (
    'if [[ -v NEXT_PUBLIC_API_BASE_URL && "$NEXT_PUBLIC_API_BASE_URL" != "$configured_base" ]]; then',
    'die "NEXT_PUBLIC_API_BASE_URL environment override must match .env"',
):
    weakened = library.replace(missing_marker, "missing", 1)
    try:
        assert_public_api_base_override_contract(weakened)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"public API base override contract accepted missing guard: {missing_marker}"
        )


def assert_process_environment_contract(source: str) -> None:
    function_start = source.index("assert_env_file_matches_process_environment() {")
    function_end = source.index("\n}\n", function_start)
    contract = source[function_start:function_end]
    for marker in (
        "assert_env_file_matches_process_environment() {",
        "require_secure_env_file",
        'mapfile -t env_lines < "$ENV_FILE"',
        'local -a env_lines=()',
        'local -A seen_keys=()',
        'case "$line" in\n      *$\'\\r\'*) die ".env contains a malformed entry" ;;',
        '[[ "$line" == *=* ]] || die ".env contains a malformed entry"',
        '[[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die ".env contains an invalid variable name"',
        '[[ ! -v "seen_keys[$key]" ]] || die ".env contains a duplicate variable name"',
        'declare -xp "$key" >/dev/null 2>&1',
        'inherited_value="${!key}"',
        '[ "$inherited_value" = "$raw_value" ] || die "$key environment override must match .env"',
    ):
        assert marker in contract, marker
    assert contract.count('for line in "${env_lines[@]}"; do') == 2
    assert "CONNECTMD_IMAGE_TAG" not in contract
    assert "CONNECTMD_COMPOSE_PROJECT_NAME" not in contract
    assert contract.index("require_secure_env_file") < contract.index(
        'mapfile -t env_lines < "$ENV_FILE"'
    )
    assert contract.index(
        '[[ "$line" == *=* ]] || die ".env contains a malformed entry"'
    ) < contract.index('declare -xp "$key" >/dev/null 2>&1')


assert_process_environment_contract(library)
for missing_marker in (
    'mapfile -t env_lines < "$ENV_FILE"',
    '[[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die ".env contains an invalid variable name"',
    '[[ ! -v "seen_keys[$key]" ]] || die ".env contains a duplicate variable name"',
    'declare -xp "$key" >/dev/null 2>&1',
    '[ "$inherited_value" = "$raw_value" ] || die "$key environment override must match .env"',
):
    weakened = library.replace(missing_marker, "missing", 1)
    try:
        assert_process_environment_contract(weakened)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"process environment contract accepted missing guard: {missing_marker}"
        )


validate_function = library[
    library.index("validate_production_env() {") : library.index(
        "\n}\n", library.index("validate_production_env() {")
    )
]
assert validate_function.index(
    "assert_env_file_matches_process_environment"
) < validate_function.index('postgres_user="')


def assert_recruiting_release_identity_contract(source: str) -> None:
    for marker in (
        "normalize_recruiting_enabled() {",
        "recruiting_enabled source_revision web_image_id",
        "recruiting_enabled release_receipt_digest source_revision stage_digest",
        "CONNECTMD_RECRUITING_ENABLED_PINNED",
        'STAGED_RECRUITING_ENABLED="$recruiting_enabled"',
        '[ "$recruiting_enabled" = "$(normalize_recruiting_enabled)" ] || die "Staged release recruiting state does not match .env"',
        '[ "$recruiting_enabled" = "$expected_recruiting" ] || die "Release receipt recruiting state does not match"',
        '[ "$recruiting_enabled" = "$(normalize_recruiting_enabled)" ] || die "Active release recruiting state does not match .env"',
        "assert_recruiting_authority_unchanged",
        "Stateful release cannot change CONNECTMD_RECRUITING_ENABLED",
        'Release acceptance recruiting state does not match',
    ):
        assert marker in source, marker


assert_recruiting_release_identity_contract(library)
assert 'Backup release recruiting state does not match .env' in restore
assert 'Rollback release recruiting state does not match .env' in rollback
for missing_marker in (
    "normalize_recruiting_enabled() {",
    "CONNECTMD_RECRUITING_ENABLED_PINNED",
    'STAGED_RECRUITING_ENABLED="$recruiting_enabled"',
    '[ "$recruiting_enabled" = "$(normalize_recruiting_enabled)" ] || die "Staged release recruiting state does not match .env"',
    '[ "$recruiting_enabled" = "$expected_recruiting" ] || die "Release receipt recruiting state does not match"',
    '[ "$recruiting_enabled" = "$(normalize_recruiting_enabled)" ] || die "Active release recruiting state does not match .env"',
    "assert_recruiting_authority_unchanged",
):
    weakened = library.replace(missing_marker, "missing")
    try:
        assert_recruiting_release_identity_contract(weakened)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"release identity accepted missing recruiting binding: {missing_marker}"
        )


assert "Every PostgreSQL password must be hexadecimal" in library
assert "apply_database_role_contract" in library
assert "bootstrap_database_roles" in library
assert "reconcile_database_roles" in library
assert "verify_database_roles" in library
assert "attest_restore_migrator_role" in library
restore_migrator_contract = library[
    library.index("attest_restore_migrator_role()") : library.index(
        "acceptance_receipt_root()"
    )
]
for marker in (
    "session_user <> 'connectmd_migrator'",
    "current_user <> 'connectmd_migrator'",
    "restore migrator role attributes failed",
    "restore migrator role membership failed",
    "has_database_privilege(current_user, current_database(), 'CREATE')",
    "has_database_privilege(current_user, current_database(), 'TEMPORARY')",
    "has_schema_privilege(current_user, 'public', 'CREATE')",
    "psql --set ON_ERROR_STOP=1 -f -",
):
    assert marker in restore_migrator_contract

ci_role_bootstrap = continuous_integration.index(
    "Bootstrap least-privilege database roles"
)
ci_migration = continuous_integration.index("alembic upgrade head")
ci_role_reconcile = continuous_integration.index(
    "Reconcile and verify database roles"
)
ci_projection_admin = continuous_integration.index(
    "postgresql+asyncpg://connectmd_projection_admin:"
)
ci_live_integration = continuous_integration.index(
    "CONNECTMD_RUN_LIVE_INTEGRATION=1 pytest -q tests/test_live_stack.py"
)
assert ci_role_bootstrap < ci_migration < ci_role_reconcile < ci_projection_admin < ci_live_integration
assert "POSTGRES_USER: postgres" in continuous_integration
assert "postgresql+asyncpg://connectmd_api:" in continuous_integration
assert "postgresql+asyncpg://connectmd_migrator:" in continuous_integration
assert continuous_integration.count("infra/postgres/database-role-contract.sql") == 2
assert continuous_integration.count("--set connectmd_verify=true") == 1
for role in (
    "connectmd_migrator",
    "connectmd_api",
    "connectmd_search_projection",
    "connectmd_projection_admin",
    "connectmd_account_erasure",
    "connectmd_backup",
):
    assert role in database_role_contract
for forbidden_attribute in (
    "NOSUPERUSER",
    "NOCREATEDB",
    "NOCREATEROLE",
    "NOINHERIT",
    "NOREPLICATION",
    "NOBYPASSRLS",
):
    assert forbidden_attribute in database_role_contract
assert "RESET ALL" in database_role_contract
assert "CONNECTION LIMIT -1" in database_role_contract
assert "rolconfig, ARRAY[]::text[]) <> ARRAY['search_path=pg_catalog, public']" in database_role_contract
assert "session_user <> 'postgres' OR current_user <> 'postgres'" in database_role_contract
assert "ALTER DATABASE %I OWNER TO %I" in database_role_contract
assert "current_database(), session_user" in database_role_contract
assert "pg_get_userbyid(datdba)" in database_role_contract
assert "<> 'postgres'" in database_role_contract
assert not re.search(
    r"ALTER DATABASE[^\n]*OWNER TO connectmd_migrator", database_role_contract
)
assert "has_database_privilege('connectmd_migrator',current_database(),'CREATE')" in database_role_contract
assert "has_database_privilege('connectmd_migrator',current_database(),'TEMPORARY')" in database_role_contract
assert "table ACL contract has missing or surplus authority" in database_role_contract
assert "sequence ACL contract has missing or surplus authority" in database_role_contract
assert "column ACL contract has surplus authority" in database_role_contract
assert "CREATE ROLE" not in (root / "apps/api/alembic/versions/0019_search_projection_outbox.py").read_text(encoding="utf-8")

rollback_probe = rollback.index("alembic current --check-heads")
rollback_taxonomy_preflight = rollback.index(
    "taxonomy-admin python -m app.cli taxonomy verify"
)
rollback_exact_preflight = rollback.index(
    "exact-search-admin python -m app.cli exact-search verify"
)
assert "SEARCH_PROJECTION_CONTRACT_VERSION as version" in rollback
rollback_exact_image_contract = rollback.index("assert_exact_search_image_contract")
rollback_validate = rollback.index("validate_production_env")
rollback_pepper_guard = rollback.index("assert_api_key_pepper_unchanged")
rollback_clean_source = rollback.index("ensure_clean_source")
rollback_receipt = rollback.index('load_release_receipt "$image_tag"')
rollback_acceptance = rollback.index('load_release_acceptance "$image_tag"')
rollback_identity = rollback.index(
    'assert_release_images_match "$image_tag" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"'
)
rollback_authority_probe = rollback.index("DELETION_AUTHORITY_CONTRACT_VERSION")
rollback_stop = rollback.index(
    "compose --profile account-lifecycle stop account-erasure-worker search-projection-worker nginx frontend api converter"
)
rollback_rebuild = rollback.index("search-admin python -m app.cli rebuild-search")
rollback_taxonomy_stable = rollback.index(
    "taxonomy-admin python -m app.cli taxonomy verify", rollback_stop
)
rollback_exact_backfill = rollback.index(
    "exact-search-admin python -m app.cli exact-search backfill --if-required",
    rollback_stop,
)
rollback_exact_stable = rollback.index(
    "exact-search-admin python -m app.cli exact-search verify", rollback_stop
)
rollback_start = rollback.index(
    "compose up -d --no-build converter search-projection-worker api frontend nginx"
)
assert (
    rollback_probe
    < rollback_taxonomy_preflight
    < rollback_exact_preflight
    < rollback_stop
    < rollback_taxonomy_stable
    < rollback_exact_backfill
    < rollback_exact_stable
    < rollback_rebuild
    < rollback_start
)
assert (
    rollback_clean_source
    < rollback_validate
    < rollback_pepper_guard
    < rollback_receipt
    < rollback_acceptance
    < rollback_identity
    < rollback_authority_probe
    < rollback_exact_image_contract
    < rollback_stop
)
assert "stop_failed_rollback_on_exit" in rollback
rollback_lifecycle_state = rollback.index(
    'lifecycle_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"'
)
rollback_lifecycle_probe = rollback.index("ACCOUNT_LIFECYCLE_HEALTH_CONTRACT_VERSION")
rollback_lifecycle_start = rollback.index(
    "compose --profile account-lifecycle up -d --no-build account-erasure-worker"
)
rollback_lifecycle_wait = rollback.index(
    "wait_for_profiled_service account-lifecycle account-erasure-worker",
    rollback_lifecycle_start,
)
rollback_persist = rollback.index('persist_image_tag "$image_tag"')
assert rollback_pepper_guard < rollback_persist
assert (
    rollback.index('assert_service_image_identity api "$RELEASE_API_IMAGE_ID"')
    > rollback_start
)
assert (
    rollback.index('assert_service_image_identity frontend "$RELEASE_WEB_IMAGE_ID"')
    > rollback_start
)
assert (
    rollback.index('assert_service_image_identity nginx "$RELEASE_NGINX_IMAGE_ID"')
    > rollback_start
)
assert rollback_lifecycle_state < rollback_lifecycle_probe < rollback_stop
assert (
    rollback_start
    < rollback_lifecycle_start
    < rollback_lifecycle_wait
    < rollback_persist
)
assert 'if [ "$lifecycle_should_run" = true ]; then' in rollback
assert 'if [ "${lifecycle_enabled:-false}" = "true" ]; then' in rollback
assert 'if [ "$lifecycle_should_pause" = true ]; then' in rollback
assert (
    'profiled_service_state account-lifecycle account-erasure-worker)" = "paused"'
    in rollback
)
assert '[ "$#" -eq 1 ] || die "Usage: ${0##*/} FULL_TARGET_REVISION"' in update
assert 'is_full_source_revision "$target_revision"' in update
assert 'git -C "$REPO_ROOT" fetch --tags --prune origin' in update
assert 'git -C "$REPO_ROOT" pull --ff-only' not in update
assert 'git -C "$REPO_ROOT" merge --ff-only "$target_revision"' in update
assert (
    'merge-base --is-ancestor "$current_source_revision_value" "$target_revision"'
    in update
)
assert 'merge-base --is-ancestor "$target_revision" "$upstream_ref"' in update
assert update.index("fetch --tags --prune origin") < update.index(
    'merge-base --is-ancestor "$current_source_revision_value" "$target_revision"'
)
assert update.index("load_active_release_identity") < update.index(
    'bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/backup.sh"'
)
# These exact count/order gates are intentional mutation counterexamples:
# removal fails the count/index lookup, and post-fetch placement fails ordering.
update_lock = update.index("acquire_operation_lock")
update_validate = update.index("validate_production_env")
update_clean_source = update.index("ensure_clean_source")
update_fetch = update.index('git -C "$REPO_ROOT" fetch --tags --prune origin')
update_no_change = update.index(
    'if [ "$target_revision" = "$current_source_revision_value" ]; then'
)
assert update.count("validate_production_env") == 1
assert (
    update_lock
    < update_validate
    < update_clean_source
    < update_fetch
    < update_no_change
)

# TLS may read validated values only after taking the shared lock. Removal or
# movement past the first Compose/certificate mutation must fail this contract.
tls_lock = tls.index("acquire_operation_lock")
tls_validate = tls.index("validate_production_env")
tls_selector = tls.index("select_release_image_tag staged-or-accepted")
tls_hostname = tls.index('domain="$(require_hostname)"')
tls_first_mutation = tls.index("compose up -d --no-build nginx")
assert tls.count("validate_production_env") == 1
assert tls.count("select_release_image_tag staged-or-accepted") == 1
assert tls_lock < tls_validate < tls_selector < tls_hostname < tls_first_mutation
assert "PREVIOUS_SOURCE_REVISION" in update
assert "TARGET_SOURCE_REVISION" in update
assert "STAGED_SOURCE_REVISION" in update
assert "release-accept.sh --yes-accept" in update
assert "wait_for_profiled_service" in library
profiled_wait = library[
    library.index("wait_for_profiled_service()") : library.index(
        "current_image_tag()", library.index("wait_for_profiled_service()")
    )
]
assert ".State.Health" in profiled_wait
assert "healthy | running" in profiled_wait
assert "unhealthy | exited | dead" in profiled_wait
assert "stable_checks=$((stable_checks + 1))" in profiled_wait
assert '[ "$stable_checks" -ge 3 ]' in profiled_wait
assert "sleep 1" in profiled_wait
assert 'if [ "${lifecycle_enabled:-false}" = "true" ]; then' in deploy
assert (
    deploy.index("worker_was_running=true", deploy.index("lifecycle_enabled="))
    < deploy_stop
)

rebuild_stop = rebuild.index(
    "compose --profile account-lifecycle stop account-erasure-worker search-projection-worker api"
)
rebuild_admin = rebuild.index("search-admin python -m app.cli rebuild-search")
rebuild_journal_live = rebuild.index("deletion-journal verify-live")
rebuild_taxonomy_verify = rebuild.index(
    "taxonomy-admin python -m app.cli taxonomy verify"
)
rebuild_lifecycle_start = rebuild.index(
    "compose --profile account-lifecycle up -d --no-build account-erasure-worker"
)
rebuild_lifecycle_wait = rebuild.index(
    "wait_for_profiled_service account-lifecycle account-erasure-worker",
    rebuild_lifecycle_start,
)
rebuild_complete = rebuild.index("rebuild_complete=true")
assert rebuild_stop < rebuild_journal_live < rebuild_taxonomy_verify < rebuild_admin
assert (
    rebuild_admin < rebuild_lifecycle_start < rebuild_lifecycle_wait < rebuild_complete
)
assert "stop_failed_rebuild_on_exit" in rebuild
assert "profiled_service_state account-lifecycle account-erasure-worker" in rebuild
assert "profiled_service_is_active account-lifecycle account-erasure-worker" in rebuild
assert "wait_for_profiled_service account-lifecycle account-erasure-worker" in rebuild
assert 'if [ "${lifecycle_enabled:-false}" = "true" ]; then' in rebuild
assert (
    rebuild.index("lifecycle_was_running=true", rebuild.index("lifecycle_enabled="))
    < rebuild_stop
)
assert 'if [ "$lifecycle_should_pause" = true ]; then' in rebuild
assert rebuild.count("lifecycle_should_pause=true") == 1
assert (
    rebuild.index('if [ "$lifecycle_was_running" = true ]; then')
    < rebuild_lifecycle_start
)

taxonomy_rebuild_stop = rebuild_taxonomy.index(
    "compose --profile account-lifecycle stop account-erasure-worker search-projection-worker api"
)
taxonomy_rebuild_journal = rebuild_taxonomy.index("deletion-journal verify-live")
taxonomy_rebuild_backfill = rebuild_taxonomy.index(
    "taxonomy-admin python -m app.cli taxonomy backfill"
)
taxonomy_rebuild_verify = rebuild_taxonomy.index(
    "taxonomy-admin python -m app.cli taxonomy verify"
)
taxonomy_rebuild_search = rebuild_taxonomy.index(
    "search-admin python -m app.cli rebuild-search"
)
taxonomy_rebuild_start = rebuild_taxonomy.index(
    "compose up -d --no-build search-projection-worker api"
)
assert (
    taxonomy_rebuild_stop
    < taxonomy_rebuild_journal
    < taxonomy_rebuild_backfill
    < taxonomy_rebuild_verify
    < taxonomy_rebuild_search
    < taxonomy_rebuild_start
)
assert "stop_failed_rebuild_on_exit" in rebuild_taxonomy
assert "TAXONOMY_REBUILD=PASS" in rebuild_taxonomy
assert "MEILISEARCH_REBUILD=PASS" in rebuild_taxonomy
assert (
    'profiled_service_state account-lifecycle account-erasure-worker)" = "paused"'
    in rebuild
)

for selector_source, selector_call, first_action in (
    (reconfigure, "select_release_image_tag accepted-only", "compose config -q"),
    (
        rebuild,
        "select_release_image_tag staged-or-accepted",
        "compose up -d --no-build postgres meilisearch",
    ),
    (
        rebuild_taxonomy,
        "select_release_image_tag staged-or-accepted",
        "compose up -d --no-build postgres meilisearch",
    ),
    (
        backup,
        "select_release_image_tag accepted-only",
        "\nstop_api_and_erasure_worker\n",
    ),
):
    assert selector_source.count(selector_call) == 1
    assert selector_source.index(selector_call) < selector_source.index(first_action)
assert "active_image_tag" not in reconfigure
assert reconfigure.index("select_release_image_tag accepted-only") < reconfigure.index(
    'image_tag="$RELEASE_IMAGE_TAG"'
)

selector_start = library.index("select_release_image_tag()")
selector_end = library.index("\npersist_image_tag()", selector_start)
selector = library[selector_start:selector_end]
assert '[[ -v CONNECTMD_IMAGE_TAG ]]' in selector
assert "CONNECTMD_IMAGE_TAG must not be inherited" in selector
assert "accepted-only | staged-or-accepted" in selector
assert "assert_no_pending_staged_release" in selector
assert '[ "$STAGED_SOURCE_REVISION" = "$current_source" ]' in selector
assert (
    'assert_release_images_match "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" '
    '"$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID"'
    in selector
)
assert "No accepted or staged release identity is available" in selector
assert 'export CONNECTMD_IMAGE_TAG="$selected_tag"' in selector
assert "first-deploy" not in selector


def assert_release_selector_contract(source: str) -> None:
    start = source.index("select_release_image_tag()")
    end = source.index("\npersist_image_tag()", start)
    implementation = source[start:end]
    assert "[[ -v CONNECTMD_IMAGE_TAG ]]" in implementation
    assert '[ "$STAGED_SOURCE_REVISION" = "$current_source" ]' in implementation
    assert (
        'assert_release_images_match "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" '
        '"$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID"'
        in implementation
    )
    assert "No accepted or staged release identity is available" in implementation
    assert 'export CONNECTMD_IMAGE_TAG="$selected_tag"' in implementation


for selector_mutation_name, weakened_selector in (
    (
        "inherited image-tag guard",
        library.replace(
            "[[ -v CONNECTMD_IMAGE_TAG ]]", "[[ ! -v CONNECTMD_IMAGE_TAG ]]", 1
        ),
    ),
    (
        "staged source binding",
        library.replace(
            '[ "$STAGED_SOURCE_REVISION" = "$current_source" ]',
            '[ "$STAGED_SOURCE_REVISION" != "$current_source" ]',
            1,
        ),
    ),
    (
        "neither-marker fail-closed branch",
        library.replace(
            'die "No accepted or staged release identity is available"',
            "return 0",
            1,
        ),
    ),
):
    try:
        assert_release_selector_contract(weakened_selector)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError(
            f"release image selector accepted weakened {selector_mutation_name} mutation"
        )

reconfigure_core_start = reconfigure.index(
    "compose up -d --no-build --force-recreate converter search-projection-worker api frontend nginx"
)


def assert_reconfigure_recruiting_release_gate(source: str) -> None:
    read_flag = (
        'recruiting_enabled="$(read_env_optional_value CONNECTMD_RECRUITING_ENABLED)" '
        '|| die "CONNECTMD_RECRUITING_ENABLED must appear at most once in .env"'
    )
    default_flag = 'recruiting_enabled="${recruiting_enabled:-false}"'
    refuse_enablement = (
        'die "Recruiting enablement requires a newly staged and accepted release"'
    )
    for marker in (
        read_flag,
        default_flag,
        'if [ "$recruiting_enabled" = "true" ]; then',
        refuse_enablement,
    ):
        assert marker in source, marker
    assert source.index(read_flag) < source.index(default_flag)
    assert source.index(default_flag) < source.index(refuse_enablement)
    assert source.index(refuse_enablement) < source.index("image_tag=")
    assert source.index(refuse_enablement) < source.index(
        "compose up -d --no-build --force-recreate converter search-projection-worker api frontend nginx"
    )


assert_reconfigure_recruiting_release_gate(reconfigure)
for missing_marker in (
    'if [ "$recruiting_enabled" = "true" ]; then',
    'die "Recruiting enablement requires a newly staged and accepted release"',
):
    counterexample = reconfigure.replace(missing_marker, "missing", 1)
    try:
        assert_reconfigure_recruiting_release_gate(counterexample)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"reconfigure accepted missing recruiting release gate: {missing_marker}"
        )

reconfigure_trap = reconfigure.index("stop_failed_reconfigure_on_exit()")
reconfigure_barrier = reconfigure.index("reconfigure_barrier_entered=true")
reconfigure_stop = reconfigure.index(
    "compose --profile account-lifecycle stop account-erasure-worker nginx frontend api search-projection-worker converter >/dev/null",
    reconfigure_barrier,
)
reconfigure_exact_backfill = reconfigure.index(
    "exact-search-admin python -m app.cli exact-search backfill --if-required"
)
reconfigure_exact_verify = reconfigure.index(
    "exact-search-admin python -m app.cli exact-search verify"
)
reconfigure_worker_start = reconfigure.index(
    "compose --profile account-lifecycle up -d --no-build --force-recreate account-erasure-worker"
)
reconfigure_worker_wait = reconfigure.index(
    "wait_for_profiled_service account-lifecycle account-erasure-worker"
)
reconfigure_complete = reconfigure.index("reconfigure_complete=true")
reconfigure_success = reconfigure.index("RECONFIGURED_IMAGE_TAG=")
assert (
    reconfigure.index("worker_prior_state=")
    < reconfigure_trap
    < reconfigure_barrier
    < reconfigure_stop
    < reconfigure_exact_backfill
    < reconfigure_exact_verify
    < reconfigure_core_start
    < reconfigure_worker_start
    < reconfigure_worker_wait
    < reconfigure_complete
    < reconfigure_success
)
assert (
    "reconfigure_complete=false"
    in reconfigure[
        reconfigure_trap - len("reconfigure_complete=false\n") : reconfigure_trap
    ]
)
assert (
    "trap stop_failed_reconfigure_on_exit EXIT"
    in reconfigure[reconfigure_trap:reconfigure_barrier]
)
reconfigure_failure_trap = reconfigure[
    reconfigure_trap : reconfigure.index(
        "trap stop_failed_reconfigure_on_exit EXIT", reconfigure_trap
    )
]
assert (
    'if [ "$status" -ne 0 ] && [ "$reconfigure_barrier_entered" = true ] && [ "$reconfigure_complete" = false ]; then'
    in reconfigure_failure_trap
)
assert (
    "compose --profile account-lifecycle stop account-erasure-worker nginx frontend api search-projection-worker converter"
    in reconfigure_failure_trap
)
assert (
    "failed reconfigure left application services stopped for explicit recovery"
    in reconfigure_failure_trap
)
assert 'if [ "${lifecycle_enabled:-false}" = "true" ]; then' in reconfigure
assert (
    reconfigure.index("worker_should_run=true", reconfigure.index("lifecycle_enabled="))
    < reconfigure_core_start
)
assert 'if [ "$worker_should_run" = true ]; then' in reconfigure
assert 'if [ "$worker_should_pause" = true ]; then' in reconfigure
assert reconfigure.count("worker_should_pause=true") == 1
assert (
    reconfigure.index('if [ "$worker_should_run" = true ]; then')
    < reconfigure_worker_start
)
assert (
    'profiled_service_state account-lifecycle account-erasure-worker)" = "paused"'
    in reconfigure
)

authority_roles = {
    "CONNECTMD_VERIFICATION_REVIEWER_ROLE": "recruiting_verifier",
    "CONNECTMD_POST_MODERATOR_ROLE": "content_moderator",
    "CONNECTMD_APPEAL_REVIEWER_ROLE": "appeal_reviewer",
}
authority_ids = (
    "CONNECTMD_VERIFICATION_REVIEWER_ID",
    "CONNECTMD_POST_MODERATOR_ID",
    "CONNECTMD_APPEAL_REVIEWER_ID",
)
for key in (*authority_ids, *authority_roles):
    interpolation = f"{key}: ${{{key}:?Set {key} in .env}}"
    assert compose.count(interpolation) == 2
    assert f"{key}=" in environment_example
for key in authority_ids:
    assert f"{key}=replace-me-" in environment_example
    shell_name = key.removeprefix("CONNECTMD_").lower()
    assert f'{shell_name}="$(require_secret_value {key})"' in library
for key, role in authority_roles.items():
    assert f"{key}={role}" in environment_example
    shell_name = key.removeprefix("CONNECTMD_").lower()
    assert f'{shell_name}="$(read_env_value {key})"' in library
    assert f'[ "${shell_name}" = "{role}" ]' in library
assert '[ "$appeal_reviewer_id" != "$post_moderator_id" ]' in library

backup_state_capture = backup.index("worker_prior_state=")
backup_stop = backup.index("\nstop_api_and_erasure_worker\n", backup_state_capture)
backup_writer_stop_proof = backup.index(
    "service_is_active api || profiled_service_is_active account-lifecycle account-erasure-worker",
    backup_stop,
)
backup_artifact_preflight = backup.index(
    "\nassert_no_artifact_staging\n", backup_writer_stop_proof
)
backup_journal_live = backup.index("deletion-journal verify-live", backup_stop)
backup_checkpoint = backup.index("deletion-journal checkpoint", backup_journal_live)
backup_mutation_boundary = backup.index("restart_allowed=false", backup_checkpoint)
backup_registration_reconcile = backup.index(
    "assert_existing_backup_artifacts_registered", backup_mutation_boundary
)
backup_dump = backup.index("pg_dump", backup_checkpoint)
backup_restore = backup.index("restore_original_services", backup_dump)
backup_complete = backup.index("BACKUP=", backup_restore)
assert (
    backup_stop
    < backup_writer_stop_proof
    < backup_artifact_preflight
    < backup_journal_live
    < backup_checkpoint
    < backup_mutation_boundary
    < backup_registration_reconcile
    < backup_dump
    < backup_restore
    < backup_complete
)
assert backup_state_capture < backup_stop
assert "database-backup" in backup
assert "--no-owner --no-privileges" in backup
assert "verify_database_roles" in backup


def assert_backup_artifact_preflight_contract(source: str) -> None:
    function_start = source.index("assert_no_artifact_staging() {")
    function_end = source.index("\n}\n", function_start)
    preflight = source[function_start:function_end]
    for marker in (
        'VersionStore("/app/storage")',
        ".scan_staged_artifacts()",
        "scan.descriptors",
        "scan.incomplete_payloads",
        "scan.invalid_entry",
        "scan.overbound",
        "except Exception:",
        "raise SystemExit(2)",
        "raise SystemExit(1)",
    ):
        assert marker in preflight
    stop = source.index("\nstop_api_and_erasure_worker\n")
    stopped = source.index(
        "service_is_active api || profiled_service_is_active account-lifecycle account-erasure-worker",
        stop,
    )
    called = source.index("\nassert_no_artifact_staging\n", stopped)
    capture = source.index("pg_dump", called)
    mutation = source.index("restart_allowed=false", called)
    assert stop < stopped < called < mutation < capture


assert_backup_artifact_preflight_contract(backup)
for weakened_backup in (
    backup.replace("\nassert_no_artifact_staging\n", "\n", 1),
    backup.replace("scan.descriptors", "False", 1),
    backup.replace("scan.incomplete_payloads", "False", 1),
    backup.replace("scan.invalid_entry", "False", 1),
    backup.replace("scan.overbound", "False", 1),
    backup.replace("except Exception:", "except StorageIntegrityError:", 1),
):
    try:
        assert_backup_artifact_preflight_contract(weakened_backup)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError("weakened artifact staging preflight unexpectedly passed")

backup_clean_source = backup.index("ensure_clean_source")
backup_active_identity = backup.index("assert_active_release_identity")
assert backup_clean_source < backup_active_identity < backup_stop
assert "connectmd-backup-v3" in backup
assert "git_revision" not in backup
assert "unavailable" not in backup
for backup_identity_field in (
    "release_source_revision",
    "release_image_tag",
    "release_api_image_id",
    "release_web_image_id",
    "release_nginx_image_id",
    "release_receipt_digest",
    "release_acceptance_digest",
):
    assert backup_identity_field in backup
assert 'if [ "${lifecycle_enabled:-false}" = "true" ]; then' in backup
assert (
    backup.index("worker_should_run=true", backup.index("lifecycle_enabled="))
    < backup_stop
)
backup_restore_function = backup[
    backup.index("restore_original_services()") : backup.index(
        "write_registration_receipt()"
    )
]
assert 'if [ "$worker_should_run" = true ]; then' in backup_restore_function
assert backup.count("worker_should_pause=true") == 1
assert backup_restore_function.index(
    'if [ "$worker_should_run" = true ]; then'
) < backup_restore_function.index(
    "compose --profile account-lifecycle up -d --no-build account-erasure-worker"
)
assert "wait_for_service api" in backup_restore_function
assert (
    "wait_for_profiled_service account-lifecycle account-erasure-worker"
    in backup_restore_function
)
assert 'if [ "$worker_should_pause" = true ]; then' in backup_restore_function
assert (
    'profiled_service_state account-lifecycle account-erasure-worker)" = "paused"'
    in backup_restore_function
)
assert "if ! (restore_original_services); then" in backup
assert backup.index("restart_allowed=false", backup_stop) > backup_checkpoint
for checkpoint_field in (
    "deletion_journal_head_sequence",
    "deletion_journal_head_digest",
):
    assert checkpoint_field in backup
assert "CONNECTMD_LIFECYCLE_HMAC_KEY_SHA256" in library
assert "CONNECTMD_LIFECYCLE_AEAD_KEY_SHA256" in library
assert "CONNECTMD_API_KEY_PEPPER_SHA256" in library
assert "CONNECTMD_ACCOUNT_LIFECYCLE_PINNED" in library
assert "Account lifecycle activation is one-way and cannot be disabled" in library
for distinct_lifecycle_authority_marker in (
    "Lifecycle HMAC and AEAD keys must be distinct",
    "Lifecycle HMAC and deletion witness HMAC keys must be distinct",
    "Lifecycle AEAD and deletion witness HMAC keys must be distinct",
):
    assert distinct_lifecycle_authority_marker in library
assert environment_example.count("CONNECTMD_DELETION_WITNESS_DIR=") == 1
assert environment_example.count("CONNECTMD_DELETION_WITNESS_HMAC_KEY=") == 1
witness_dir_example = next(
    line.split("=", 1)[1]
    for line in environment_example.splitlines()
    if line.startswith("CONNECTMD_DELETION_WITNESS_DIR=")
)
witness_key_example = next(
    line.split("=", 1)[1]
    for line in environment_example.splitlines()
    if line.startswith("CONNECTMD_DELETION_WITNESS_HMAC_KEY=")
)
lifecycle_hmac_example = next(
    line.split("=", 1)[1]
    for line in environment_example.splitlines()
    if line.startswith("CONNECTMD_LIFECYCLE_HMAC_KEY=")
)
lifecycle_aead_example = next(
    line.split("=", 1)[1]
    for line in environment_example.splitlines()
    if line.startswith("CONNECTMD_LIFECYCLE_AEAD_KEY=")
)
assert witness_dir_example.startswith("/")
authority_key_examples = {
    lifecycle_hmac_example,
    lifecycle_aead_example,
    witness_key_example,
}
assert all(len(value) >= 32 for value in authority_key_examples)
assert len(authority_key_examples) == 3

for release_field in (
    "CONNECTMD_RELEASE_FORMAT=connectmd-release-v3",
    "CONNECTMD_SOURCE_REVISION",
    "CONNECTMD_API_IMAGE_ID",
    "CONNECTMD_WEB_IMAGE_ID",
    "CONNECTMD_NGINX_IMAGE_ID",
    "CONNECTMD_RELEASE_RECEIPT_SHA256",
    "CONNECTMD_ACCEPTANCE_RECEIPT_SHA256",
    "CONNECTMD_API_KEY_PEPPER_SHA256",
    "CONNECTMD_DELETION_WITNESS_HMAC_KEY_SHA256",
    "CONNECTMD_DELETION_WITNESS_DIR_SHA256",
):
    assert release_field in library
for release_identity_helper in (
    "is_full_source_revision()",
    "is_image_identity()",
    "assert_release_images_match()",
    "write_release_receipt()",
    "load_release_receipt()",
    "load_active_release_identity()",
    "assert_active_release_identity()",
):
    assert release_identity_helper in library
assert "connectmd-release-receipt-v1" in library
assert "Pre-existing release images lack an immutable historical receipt" in library
assert "Partial pre-existing release image set is unsafe" in library
assert (
    "Refusing to create a release receipt from pre-existing images without a verified build"
    in library
)
reuse_guard = library[
    library.index("build_or_reuse_release_images()") : library.index(
        "apply_database_role_contract()"
    )
]
assert reuse_guard.index('load_release_receipt "$image_tag"') < reuse_guard.index(
    "REUSING_IMAGE="
)
assert reuse_guard.index(
    "Pre-existing release images lack an immutable historical receipt"
) < reuse_guard.index("REUSING_IMAGE=")
assert 'export CONNECTMD_RELEASE_IMAGES_BUILT_FOR_TAG="$image_tag"' in reuse_guard
assert (
    'witness_dir_hash="$(printf \'%s\' "$(realpath -m "$(read_env_value CONNECTMD_DELETION_WITNESS_DIR)")" | sha256sum'
    in library
)
assert (
    "for key in CONNECTMD_LIFECYCLE_HMAC_KEY CONNECTMD_LIFECYCLE_AEAD_KEY CONNECTMD_DELETION_WITNESS_HMAC_KEY"
    in library
)
assert "assert_api_key_pepper_unchanged()" in library
assert library.count("assert_api_key_pepper_unchanged") >= 3
assert "Stateful release cannot rotate CONNECTMD_API_KEY_PEPPER" in library
assert library.index("printf 'CONNECTMD_API_KEY_PEPPER_SHA256=%s\\n'") < library.index(
    "assert_api_key_pepper_unchanged()"
)
assert "CONNECTMD_DELETION_WITNESS_DIR must be an absolute path" in library
assert "CONNECTMD_DELETION_WITNESS_DIR must be canonical" in library
assert (
    'case "$witness_path" in "$backup_path" | "$backup_path"/*) die "Deletion witness authority must be outside CONNECTMD_BACKUP_DIR"'
    in library
)

journal_root_create = journal_init.index('mkdir -p "$journal_root"')
journal_runtime_uid_check = journal_init.index('"$(id -u)" = "$authority_uid"')
journal_runtime_gid_check = journal_init.index('"$(id -g)" = "$authority_gid"')
journal_operation_lock = journal_init.index("acquire_operation_lock")
journal_clean_source = journal_init.index("ensure_clean_source")
journal_environment_validation = journal_init.index("validate_production_env")
journal_root_mode = journal_init.index('chmod 700 "$journal_root"')
witness_owner_check = journal_init.index(
    '"$(stat -c \'%u\' "$witness_root")" = "$authority_uid"'
)
witness_group_check = journal_init.index(
    '"$(stat -c \'%g\' "$witness_root")" = "$authority_gid"'
)
witness_mode_check = journal_init.index('"$(stat -c \'%a\' "$witness_root")" = "700"')
journal_owner_check = journal_init.index(
    '"$(stat -c \'%u\' "$journal_root")" = "$authority_uid"'
)
journal_group_check = journal_init.index(
    '"$(stat -c \'%g\' "$journal_root")" = "$authority_gid"'
)
journal_mode_check = journal_init.index('"$(stat -c \'%a\' "$journal_root")" = "700"')
journal_initialize = journal_init.index("deletion-journal init")
journal_checkpoint = journal_init.index("deletion-journal checkpoint")
assert (
    journal_runtime_uid_check
    < journal_runtime_gid_check
    < journal_operation_lock
    < journal_clean_source
    < journal_environment_validation
    < journal_init.index('backup_root_path="$(backup_root)"')
    < witness_owner_check
    < witness_group_check
    < witness_mode_check
    < journal_root_create
    < journal_owner_check
    < journal_group_check
    < journal_root_mode
    < journal_mode_check
    < journal_initialize
    < journal_checkpoint
)
initializer_cleanup = journal_init[
    journal_init.index("remove_initializer_owned_api_image()") : journal_init.index(
        "cleanup_initializer_owned_api_image()"
    )
]
assert 'current_image_id="$(docker image inspect --format \'{{.Id}}\'' in initializer_cleanup
assert '[ "$current_image_id" = "$api_image_created_id" ]' in initializer_cleanup
assert initializer_cleanup.index(
    '[ "$current_image_id" = "$api_image_created_id" ]'
) < initializer_cleanup.index('docker image rm "connectmd-api:$image_tag"')
assert "docker image prune" not in journal_init
journal_image_precheck = journal_init.index(
    'if docker image inspect "connectmd-api:$image_tag"'
)
journal_preexisting_refusal = journal_init.index(
    "Source-tagged API image already exists; initializer cannot prove ownership",
    journal_image_precheck,
)
journal_image_created = journal_init.index(
    "api_image_created_by_initializer=true", journal_image_precheck
)
journal_image_build = journal_init.index("compose build api", journal_image_created)
journal_image_identity = journal_init.index(
    'api_image_created_id="$(image_identity_for_tag connectmd-api "$image_tag")"',
    journal_image_build,
)
journal_success_cleanup = journal_init.index(
    "remove_initializer_owned_api_image", journal_checkpoint
)
journal_success_print = journal_init.index(
    "DELETION_JOURNAL_AND_WITNESS_AUTHORITIES=INITIALIZED"
)
assert (
    journal_image_precheck
    < journal_preexisting_refusal
    < journal_image_created
    < journal_image_build
    < journal_image_identity
    < journal_initialize
    < journal_checkpoint
    < journal_success_cleanup
    < journal_success_print
)
assert "readonly authority_uid=10001" in journal_init
assert "readonly authority_gid=10001" in journal_init
assert "--uid 10001" in api_dockerfile
assert "--gid 10001" in api_dockerfile
assert "USER connectmd" in api_dockerfile
for unsafe_ownership_repair in (
    'chown -R "$authority_uid:$authority_gid" "$journal_root"',
    'chown -R "$authority_uid:$authority_gid" "$witness_root"',
    'authority_uid="${',
    'authority_gid="${',
):
    assert unsafe_ownership_repair not in journal_init
assert 'mkdir -p "$witness_root"' not in journal_init
assert 'chmod 700 "$witness_root"' not in journal_init
assert "Pre-create CONNECTMD_DELETION_WITNESS_DIR" in journal_init
assert 'witness_root="$(read_env_value CONNECTMD_DELETION_WITNESS_DIR)"' in journal_init
assert "CONNECTMD_DELETION_WITNESS_DIR must be absolute" in journal_init
assert "CONNECTMD_DELETION_WITNESS_DIR must be canonical" in journal_init
assert (
    'case "$witness_root" in "$backup_root_path" | "$backup_root_path"/*) die "Deletion witness authority must be outside CONNECTMD_BACKUP_DIR"'
    in journal_init
)
assert (
    "CONNECTMD_DELETION_WITNESS_DIR must not be the filesystem root" in library
    and "CONNECTMD_DELETION_WITNESS_DIR must not be the filesystem root" in journal_init
)
assert (
    "Deletion witness authority must not contain CONNECTMD_BACKUP_DIR" in library
    and "Deletion witness authority must not contain CONNECTMD_BACKUP_DIR"
    in journal_init
)
assert '[ ! -L "$witness_root" ]' in journal_init
for deployment_identity_marker in (
    "sudo addgroup --gid 10001 connectmd",
    "--uid 10001 --gid 10001 connectmd",
    'test "$(id -u connectmd)" = 10001',
    'test "$(id -g connectmd)" = 10001',
):
    assert deployment_identity_marker in deployment_guide
witness_admin_create = deployment_guide.index(
    "sudo install -d -m 0700 -o connectmd -g connectmd /var/lib/connectmd/deletion-head-witness"
)
deploy_account_checkout = deployment_guide.index("cd /srv/connectmd/app")
assert witness_admin_create < deploy_account_checkout
assert cli_source.index("verify_live_deletion_mirror") < cli_source.index(
    "await projection.reset_index()"
)

assert "python ../../infra/tests/operational-contracts.py" in continuous_integration
assert "python infra/tests/operational-contracts.py" not in continuous_integration
assert "compose stop search-projection-worker api" not in continuous_integration
assert continuous_integration.count("bash infra/tests/environment-example-contract.sh") == 1
assert "ENVIRONMENT_EXAMPLE_CONTRACT=PASS" in environment_example_contract
assert continuous_integration.count(
    "bash infra/tests/init-deletion-journal-image-contract.sh"
) == 1
assert "INIT_DELETION_JOURNAL_IMAGE_CONTRACT=PASS" in journal_image_contract
assert "https://example.clerk.accounts.dev |" in library
assert "https://example.clerk.accounts.dev/.well-known/jwks.json |" in library
assert "sk_test_replace* | sk_live_replace*" in library
assert "*example.clerk.accounts.dev*" not in library
assert 'clerk_secret_key="$(require_secret_value CLERK_SECRET_KEY)"' in library
assert 'validate_clerk_secret_key "$clerk_secret_key"' in library
assert "validate_clerk_secret_key" in environment_example_contract
assert 'validate_clerk_publishable_key "$clerk_publishable_key"' in library
assert "validate_clerk_publishable_key" in environment_example_contract
assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` must use the `pk_test_` or `pk_live_` family" in deployment_guide
assert "ending in exactly one `$`" in deployment_guide
publishable_validator = library[
    library.index("validate_clerk_publishable_key()") : library.index(
        "\nis_lowercase_dns_hostname", library.index("validate_clerk_publishable_key()")
    )
]
assert "assert " not in publishable_validator
for valid_clerk_publishable in (
    "pk_test_Zm9vLWJhci0xLmNsZXJrLmFjY291bnRzLmRldiQ=",
    "pk_live_Y2xlcmsuZXhhbXBsZS5jb20k",
):
    assert valid_clerk_publishable in environment_example_contract
for malformed_clerk_publishable in (
    "pk_test_short",
    "pk_stage_Zm9vLmJhciQ=",
    "pk_test_@@@@",
    "pk_test_bm90LWhvc3Qk",
    "pk_test_Zm9vLi5iYXI=",
):
    assert malformed_clerk_publishable in environment_example_contract
assert "PYTHONOPTIMIZE=1" in environment_example_contract
for malformed_clerk_secret in (
    "sk_test_short",
    "sk_bad_1234567890123456",
    "sk_live_123456789012345!",
):
    assert malformed_clerk_secret in environment_example_contract
for clerk_placeholder in (
    "CONNECTMD_CLERK_JWKS_URL=https://example.clerk.accounts.dev/.well-known/jwks.json",
    "CONNECTMD_CLERK_ISSUER=https://example.clerk.accounts.dev",
):
    assert clerk_placeholder in environment_example
    assert clerk_placeholder.split("=", 1)[0] in environment_example_contract
assert continuous_integration.count("bash infra/tests/hostname-contract.sh") == 1
assert "HOSTNAME_CONTRACT=PASS" in hostname_contract
for invalid_hostname in (
    "-bad.example",
    "bad-.example",
    "Example.COM",
    "bad_name.example",
    "bad..example",
):
    assert invalid_hostname in hostname_contract

rollback_checkout = deployment_guide.index(
    'git switch --detach "$previous_source_revision"'
)
rollback_command = deployment_guide.index(
    'bash infra/scripts/rollback.sh "$previous_image_tag"'
)
assert rollback_checkout < rollback_command

smoke_backup_env = https_smoke.index('set_env_value CONNECTMD_BACKUP_DIR "$BACKUP_DIR"')
smoke_witness_env = https_smoke.index(
    'set_env_value CONNECTMD_DELETION_WITNESS_DIR "$WITNESS_DIR"'
)
smoke_authority_roots = https_smoke.index(
    'mkdir -p "$BACKUP_DIR/.connectmd-lifecycle/deletion-journal" "$WITNESS_DIR"'
)
smoke_authority_chown = https_smoke.index(
    'run_as_root chown -R "$CONTAINER_UID:$CONTAINER_GID"'
)
smoke_journal_owner_check = https_smoke.index(
    "stat -c '%u:%g' \"$BACKUP_DIR/.connectmd-lifecycle/deletion-journal\""
)
smoke_witness_owner_check = https_smoke.index("stat -c '%u:%g' \"$WITNESS_DIR\"")
smoke_search_bootstrap = https_smoke.index(
    "--profile search-bootstrap run --rm --no-deps -T search-key-bootstrap"
)
smoke_role_bootstrap = https_smoke.index("apply_database_role_contract true false false")
smoke_journal_init = https_smoke.index("deletion-journal init")
smoke_journal_checkpoint = https_smoke.index("deletion-journal checkpoint")
smoke_migration = https_smoke.index("alembic upgrade head")
smoke_role_reconcile = https_smoke.index("apply_database_role_contract true true true")
smoke_taxonomy_backfill = https_smoke.index(
    "taxonomy-admin python -m app.cli taxonomy backfill --if-required"
)
smoke_taxonomy_verify = https_smoke.index(
    "taxonomy-admin python -m app.cli taxonomy verify"
)
smoke_exact_backfill = https_smoke.index(
    "exact-search-admin python -m app.cli exact-search backfill --if-required"
)
smoke_exact_verify = https_smoke.index(
    "exact-search-admin python -m app.cli exact-search verify"
)
smoke_rebuild = https_smoke.index("search-admin python -m app.cli rebuild-search")
smoke_public_start = https_smoke.index(
    '"${COMPOSE[@]}" up -d --no-build converter api frontend nginx'
)
assert (
    smoke_backup_env
    < smoke_witness_env
    < smoke_authority_roots
    < smoke_authority_chown
    < smoke_journal_owner_check
    < smoke_witness_owner_check
    < smoke_search_bootstrap
    < smoke_role_bootstrap
    < smoke_migration
    < smoke_role_reconcile
    < smoke_journal_init
    < smoke_journal_checkpoint
    < smoke_taxonomy_backfill
    < smoke_taxonomy_verify
    < smoke_exact_backfill
    < smoke_exact_verify
    < smoke_rebuild
    < smoke_public_start
)
assert "readonly CONTAINER_UID=10001" in https_smoke
assert "readonly CONTAINER_GID=10001" in https_smoke
assert "sudo --non-interactive true" in https_smoke
assert "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING" in https_smoke
assert (
    "set_env_value NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY "
    "pk_test_Zm9vLWJhci0xLmNsZXJrLmFjY291bnRzLmRldiQ="
    in https_smoke
)
assert "pk_test_tls_smoke" not in https_smoke
for database_password_key in (
    "CONNECTMD_MIGRATOR_DB_PASSWORD",
    "CONNECTMD_API_DB_PASSWORD",
    "CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD",
    "CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD",
    "CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD",
    "CONNECTMD_BACKUP_DB_PASSWORD",
):
    assert f"set_env_value {database_password_key}" in https_smoke
assert '"mode":"exact"' in https_smoke
assert 'payload["mode"] == "exact"' in https_smoke
assert (
    'assert task["artifacts"][0]["parts"][0]["data"] == {"hits": []}' not in https_smoke
)
a2a_search_assertion = https_smoke[
    https_smoke.index("assert_a2a_search() {") : https_smoke.index(
        "assert_mcp_initialize() {"
    )
]
for shared_search_field in (
    'search["offset"]',
    'search["limit"]',
    'search["total"]',
    'search["indexing_available"]',
    'search["facets"]',
    'search["taxonomy_facets"]',
    'search["warning"]',
):
    assert shared_search_field in a2a_search_assertion
cleanup_function = https_smoke[
    https_smoke.index("cleanup() {") : https_smoke.index("trap cleanup EXIT")
]
assert cleanup_function.index(
    'run_as_root chown -R "$HOST_UID:$HOST_GID" "$BACKUP_DIR" "$WITNESS_DIR"'
) < cleanup_function.index('rm -rf "$SCRATCH"')
assert "chmod 777" not in https_smoke
smoke_authority_keys = {
    line.split(maxsplit=2)[2]
    for line in https_smoke.splitlines()
    if line.startswith(
        (
            "set_env_value CONNECTMD_LIFECYCLE_HMAC_KEY ",
            "set_env_value CONNECTMD_LIFECYCLE_AEAD_KEY ",
            "set_env_value CONNECTMD_DELETION_WITNESS_HMAC_KEY ",
        )
    )
}
assert len(smoke_authority_keys) == 3
assert all(len(value) >= 32 for value in smoke_authority_keys)
assert "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED=false" in environment_example
assert "NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=false" in environment_example
assert '"get-mandate-bound-agent-outreach-status"' in https_smoke
assert '"discover-public-agents"' in https_smoke

assert "CONNECTMD_RECOVERY_INNER" in recovery_roundtrip
assert 'git -C "$repository" rev-parse --verify HEAD' in recovery_roundtrip
assert (
    "Recovery roundtrip requires an actual committed checkout HEAD"
    in recovery_roundtrip
)
assert 'git clone --quiet --no-hardlinks "$repo_root" "$worktree"' in recovery_roundtrip
assert 'setpriv --reuid "$CONTAINER_UID" --regid "$CONTAINER_GID"' in recovery_roundtrip
assert 'env HOME="$runtime_home" XDG_CONFIG_HOME="$runtime_home/.config"' in recovery_roundtrip
assert "env -u HOME" not in recovery_roundtrip
assert "docker version --format" in recovery_roundtrip
assert 'CONNECTMD_COMPOSE_PROJECT_NAME="$project_name"' in recovery_roundtrip
assert "export CONNECTMD_HTTP_BINDING=80" in recovery_roundtrip
assert "export CONNECTMD_HTTPS_BINDING=443" in recovery_roundtrip
assert "18081" not in recovery_roundtrip
assert "18444" not in recovery_roundtrip
assert "down --volumes --remove-orphans" in recovery_roundtrip
assert 'rm -rf -- "$scratch"' in recovery_roundtrip
assert (
    "set_env_value NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY "
    "pk_test_Zm9vLWJhci0xLmNsZXJrLmFjY291bnRzLmRldiQ="
    in recovery_roundtrip
)
assert "pk_test_recovery_roundtrip" not in recovery_roundtrip
assert recovery_roundtrip.count("docker image rm") == 1
assert 'docker image rm "connectmd-api:$backup_tag" >/dev/null' in recovery_roundtrip
assert (
    'docker image tag "$backup_web_id" "connectmd-api:$backup_tag"'
    in recovery_roundtrip
)
assert (
    'docker image tag "$backup_api_id" "connectmd-api:$backup_tag"'
    in recovery_roundtrip
)
assert "bash infra/scripts/init-deletion-journal.sh" in recovery_roundtrip
assert recovery_roundtrip.count("bash infra/scripts/deploy.sh") == 3
assert "Legacy v2 recovery was silently accepted" in recovery_roundtrip
assert recovery_roundtrip.count("bash infra/scripts/health.sh") == 2
assert "bash infra/scripts/backup.sh" in recovery_roundtrip
assert "seed_test_only_accepted_authority()" in recovery_roundtrip
assert (
    "Test-only acceptance fixture is restricted to recovery inner mode"
    in recovery_roundtrip
)
assert "bash infra/scripts/release-accept.sh" not in recovery_roundtrip
assert "--verify-only" in recovery_roundtrip
assert "--yes-restore" in recovery_roundtrip
assert "ci_recovery_probe" in recovery_roundtrip
assert "roundtrip.md" in recovery_roundtrip
assert "RECOVERY_ROUNDTRIP=PASS" in recovery_roundtrip
assert "-U connectmd" not in recovery_roundtrip
assert "-U postgres -d connectmd" in recovery_roundtrip
for database_password_key in (
    "CONNECTMD_MIGRATOR_DB_PASSWORD",
    "CONNECTMD_API_DB_PASSWORD",
    "CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD",
    "CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD",
    "CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD",
    "CONNECTMD_BACKUP_DB_PASSWORD",
):
    assert f"set_env_value {database_password_key}" in recovery_roundtrip
for stopped_consumer in (
    "assert_not_running nginx",
    "assert_not_running frontend",
    "assert_not_running api",
    "assert_not_running converter",
    "assert_not_running search-projection-worker",
):
    assert stopped_consumer in recovery_roundtrip
assert (
    "assert_not_running account-erasure-worker account-lifecycle" in recovery_roundtrip
)
assert ".connectmd-restore-state.env" in recovery_roundtrip
assert "Disabled lifecycle worker was created during recovery" in recovery_roundtrip
assert "assert_restore_preflight_rejected()" in recovery_roundtrip
assert (
    "Checked-out source revision does not match the backup generation"
    in recovery_roundtrip
)
assert "Release receipt API image identity does not match" in recovery_roundtrip
assert "Release image tag does not match its recorded identity" in recovery_roundtrip
assert "Required release image is unavailable" in recovery_roundtrip
assert "refresh_backup_checksum" in recovery_roundtrip
for running_consumer in (
    "assert_running nginx",
    "assert_running frontend",
    "assert_running api",
    "assert_running converter",
    "assert_running search-projection-worker",
):
    assert running_consumer in recovery_roundtrip
assert "format=connectmd-restore-state-v3" in recovery_roundtrip
assert "for prior_state in api converter projection frontend nginx; do" in recovery_roundtrip
assert '"${prior_state}_prior_state=running"' in recovery_roundtrip
assert "worker_prior_state=absent" in recovery_roundtrip
for identity_marker in (
    "CONNECTMD_SOURCE_REVISION",
    "CONNECTMD_API_IMAGE_ID",
    "CONNECTMD_WEB_IMAGE_ID",
    "CONNECTMD_NGINX_IMAGE_ID",
):
    assert identity_marker in recovery_roundtrip
recovery_ci = continuous_integration.index("bash infra/tests/recovery-roundtrip.sh")
certificate_ci = continuous_integration.index("mkdir -p .ci-certs", recovery_ci)
assert recovery_ci < certificate_ci
assert "timeout-minutes: 20" in continuous_integration[recovery_ci:certificate_ci]

for acceptance_helper in (
    "require_secure_record_file()",
    "validate_staged_release()",
    "write_staged_release()",
    "validate_acceptance_evidence()",
    "validate_acceptance_receipt()",
    "write_release_acceptance()",
    "clear_staged_release_after_acceptance()",
    "discard_staged_release_after_rollback()",
    "clear_matching_completed_restore_state()",
):
    assert acceptance_helper in library
assert "connectmd-staged-release-v1" in library
assert "connectmd-release-acceptance-v1" in library
assert "connectmd-release-acceptance-v2" in library
assert "connectmd-release-acceptance-evidence-v2" in library
assert "connectmd-backup-v3" in library
assert "Active release marker is not accepted v3 authority" in library
assert "Refusing to overwrite staged release record" in library
assert "Refusing to overwrite immutable release acceptance receipt" in library
assert "must be an immutable single-link record" in library
assert 'write_staged_release "$source_revision" "$image_tag"' in deploy
assert deploy.index("assert_no_pending_staged_release") < deploy_stop
assert deploy_stage < deploy_complete
assert "--yes-accept" in release_accept
assert "assert_direct_system_trust_environment" in release_accept
assert "run_with_direct_system_trust openssl s_client" in release_accept
assert "run_with_direct_system_trust openssl x509" in release_accept
assert "DIRECT_SYSTEM_TRUST_ENVIRONMENT_VARIABLES" in library
assert "CURL_CA_BUNDLE" in library
assert "SSL_CERT_FILE" in library
assert "SSL_CERT_DIR" in library
assert "OPENSSL_CONF" in library
assert "HTTPS_PROXY" in library
assert "RES_OPTIONS" in library
assert "DIRECT_SYSTEM_COMMAND_PATH" in library
assert 'PATH="$DIRECT_SYSTEM_COMMAND_PATH"' in library
release_curl_calls = [
    line
    for line in release_accept.splitlines()
    if "run_with_direct_system_trust curl" in line
]
assert len(release_curl_calls) >= 3
assert all(
    "run_with_direct_system_trust curl -q" in line for line in release_curl_calls
)
assert not any(
    line.lstrip().startswith("curl ") for line in release_accept.splitlines()
)
assert "--noproxy '*'" in release_accept
assert "--proto '=https'" in release_accept
assert "--resolve" not in release_accept
assert "--insecure" not in release_accept
assert "--cacert" not in release_accept
assert "X-Connectmd-Release-Tag" in release_accept
assert "Strict-Transport-Security" in release_accept
assert "exact_search_sha256" in release_accept
assert "connectmd-release-acceptance-evidence-v2" in release_accept

agent_readme_probe = (
    'public_get_agent_readme "$workdir/agent-readme.md" "$workdir/agent-readme.headers"'
)
agent_readme_assertion = (
    'assert_agent_readme "$workdir/agent-readme.md" "$workdir/agent-readme.headers"'
)
agent_readme_markers = (
    "# connect.md agent onboarding README",
    "## Onboarding sequence",
    "Idempotency-Key",
    "If-Match",
)


def assert_release_accept_agent_readme_contract(source: str) -> None:
    public_probe = source.index('public_get / "$workdir/root.body"')
    probe = source.index(agent_readme_probe, public_probe)
    assertion = source.index(agent_readme_assertion, probe)
    receipt = source.index('acceptance_receipt="$(write_release_acceptance', assertion)
    assert public_probe < probe < assertion < receipt

    contract_start = source.index("public_get_agent_readme() {")
    contract_end = source.index("\npublic_post() {", contract_start)
    contract = source[contract_start:contract_end]
    for anchor in (
        '--max-filesize "$agent_readme_max_bytes"',
        "--write-out '%{http_code}'",
        '[ "$status" = "200" ]',
        "text/markdown",
        'body_bytes="$(wc -c < "$body"',
        '[ "$body_bytes" -le "$agent_readme_max_bytes" ]',
        *agent_readme_markers,
    ):
        assert anchor in contract

    assert 'cat "$workdir/agent-readme.md"' not in source
    assert 'cat "$workdir/agent-readme.headers"' not in source


assert_release_accept_agent_readme_contract(release_accept)

for mutation_name, weakened_acceptance in (
    (
        "missing agent README request",
        release_accept.replace(agent_readme_probe, "", 1),
    ),
    (
        "missing agent README assertion",
        release_accept.replace(agent_readme_assertion, "", 1),
    ),
    (
        "unbounded agent README response",
        release_accept.replace('--max-filesize "$agent_readme_max_bytes"', "", 1),
    ),
    (
        "non-Markdown agent README media type",
        release_accept.replace("text/markdown", "text/plain", 1),
    ),
    *(
        (
            f"missing agent README marker: {marker}",
            release_accept.replace(marker, "", 1),
        )
        for marker in agent_readme_markers
    ),
):
    try:
        assert_release_accept_agent_readme_contract(weakened_acceptance)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError(
            f"release acceptance accepted weakened agent README gate: {mutation_name}"
        )

acceptance_runtime_services = (
    "postgres",
    "meilisearch",
    "converter",
    "search-projection-worker",
    "api",
    "frontend",
    "nginx",
)
acceptance_runtime_loop = (
    "for service in " + " ".join(acceptance_runtime_services) + "; do\n"
    '  wait_for_service "$service" "$acceptance_service_health_attempts"\n'
    "done"
)
acceptance_lifecycle_gate = (
    'if [ "${lifecycle_enabled:-false}" = "true" ]; then\n'
    "  wait_for_profiled_service account-lifecycle account-erasure-worker "
    '"$acceptance_lifecycle_health_attempts"\n'
    "fi"
)


def assert_release_accept_runtime_health_contract(source: str) -> None:
    assert_release_accept_agent_readme_contract(source)
    identity = source.index(
        'assert_release_images_match "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" '
        '"$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID"'
    )
    runtime_health = source.index(acceptance_runtime_loop)
    lifecycle_health = source.index(acceptance_lifecycle_gate, runtime_health)
    public_probe = source.index('public_get / "$workdir/root.body"', lifecycle_health)
    receipt = source.index('acceptance_receipt="$(write_release_acceptance', public_probe)
    promotion = source.index("persist_image_tag", receipt)
    assert identity < runtime_health < lifecycle_health < public_probe < receipt < promotion


assert_release_accept_runtime_health_contract(release_accept)
for required_service in acceptance_runtime_services:
    weakened_loop = acceptance_runtime_loop.replace(
        f" {required_service}", "", 1
    )
    weakened_acceptance = release_accept.replace(
        acceptance_runtime_loop, weakened_loop, 1
    )
    try:
        assert_release_accept_runtime_health_contract(weakened_acceptance)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError(
            f"release acceptance health contract accepted missing service: {required_service}"
        )

weakened_lifecycle_acceptance = release_accept.replace(
    acceptance_lifecycle_gate, "", 1
)
try:
    assert_release_accept_runtime_health_contract(weakened_lifecycle_acceptance)
except (AssertionError, ValueError):
    pass
else:
    raise AssertionError(
        "release acceptance health contract accepted a missing lifecycle-worker gate"
    )

runtime_health_start = release_accept.index(
    "readonly acceptance_service_health_attempts=30"
)
runtime_health_end = release_accept.index("\n# A retry after", runtime_health_start)
runtime_health_block = release_accept[runtime_health_start:runtime_health_end]
post_promotion_acceptance = (
    release_accept[:runtime_health_start]
    + release_accept[runtime_health_end:]
    + "\n"
    + runtime_health_block
    + "\n"
)
try:
    assert_release_accept_runtime_health_contract(post_promotion_acceptance)
except (AssertionError, ValueError):
    pass
else:
    raise AssertionError(
        "release acceptance health contract accepted a post-promotion health gate"
    )

assert "connectmd-release-acceptance-v1" in acceptance_state_contract
assert "connectmd-release-acceptance-v2" in acceptance_state_contract
assert "v2 evidence missing exact-search digest" in acceptance_state_contract
assert "load_staged_release" in release_accept
assert (
    "The only post-promotion durable action is clearing a matching completed"
    in release_accept
)
assert (
    'if [ ! -e "$STAGED_RELEASE_FILE" ] && [ ! -L "$STAGED_RELEASE_FILE" ]; then'
    in release_accept
)
assert (
    release_accept.index("write_release_acceptance")
    < release_accept.index("persist_image_tag")
    < release_accept.index("clear_staged_release_after_acceptance")
    < release_accept.index(
        "clear_matching_completed_restore_state",
        release_accept.index("write_release_acceptance"),
    )
)
assert "acceptance-state-contract.sh" in continuous_integration
assert "ACCEPTANCE_STATE_CONTRACT=PASS" in acceptance_state_contract
assert "Counterexample unexpectedly passed" in acceptance_state_contract
for selector_contract_anchor in (
    "select_release_image_tag staged-or-accepted",
    "select_release_image_tag accepted-only",
    "neither accepted nor staged release identity",
    "inherited nonempty CONNECTMD_IMAGE_TAG",
    "inherited empty CONNECTMD_IMAGE_TAG",
    "repointed staged image identity",
    "staged source mismatch",
):
    assert selector_contract_anchor in acceptance_state_contract
assert "curl missing first -q" in acceptance_state_contract
assert "curl non-first -q" in acceptance_state_contract
assert "custom direct-trust environment" in acceptance_state_contract
assert "Caller PATH substituted" in acceptance_state_contract
assert "CONNECTMD_RELEASE_TAG: ${CONNECTMD_IMAGE_TAG:-local}" in compose
assert "X-Connectmd-Release-Tag" in https_smoke
assert "connectmd-backup-v2)" in restore
assert "connectmd-backup-v3)" in restore
assert "backup_acceptance_receipt_digest" in restore
assert "load_release_acceptance" in restore
assert "restore_relaunches_existing_acceptance" in deploy
assert "RESTORED_ACCEPTED_IMAGE_TAG" in deploy
restore_acceptance_gate = deploy[
    deploy.index("restore_relaunches_existing_acceptance=false") :
]
assert '[ "$restore_backup_format" = "connectmd-backup-v3" ]' in restore_acceptance_gate
assert (
    '[ "$RELEASE_ACCEPTANCE_DIGEST" = "$restore_backup_acceptance_digest" ]'
    in restore_acceptance_gate
)
assert "Rollback did not restore the exact prior accepted marker authority" in library
assert (
    "Active marker acceptance authority does not match this staged release"
    in release_accept
)
assert "Legacy v2 recovery was silently accepted" in recovery_roundtrip
assert "backup_acceptance_receipt_digest=none" in recovery_roundtrip
for acceptance_protocol_anchor in (
    '"search-public-documents"',
    '"get-mandate-bound-agent-outreach-status"',
    '"SearchQueryRequest"',
    '"SearchResponse"',
    "assert_oauth_consistency",
    "MCP-Protocol-Version: 2025-06-18",
):
    assert acceptance_protocol_anchor in release_accept

print("operational contracts: PASS")
