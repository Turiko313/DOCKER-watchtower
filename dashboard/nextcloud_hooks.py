"""Validation for restricted Nextcloud OCC commands."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import shlex


NEXTCLOUD_CONTAINER_NAME = "nextcloud"
MAX_COMMAND_TEXT_LENGTH = 8192
MAX_COMMANDS = 20
MAX_ARGUMENTS = 64
MAX_ARGUMENT_LENGTH = 4096
_TTY_OPTIONS = {"-i", "-t", "-it", "-ti", "--interactive", "--tty"}
_ALLOWED_USERS = {"www-data", "33", "82"}
_FORBIDDEN_TOKENS = {";", "&", "&&", "|", "||", "<", "<<", ">", ">>"}


@dataclass(frozen=True)
class NextcloudExecCommand:
    argv: tuple[str, ...]
    user: str = "www-data"


def _validation_error(line_number, message):
    raise ValueError(f"Ligne {line_number}: {message}")


def parse_nextcloud_commands(command_text):
    """Return safe Docker-exec arguments from the supported text format.

    Only OCC commands targeting the fixed ``nextcloud`` container are accepted.
    The returned arguments are passed directly to Docker's exec API, never a shell.
    """
    if not isinstance(command_text, str):
        raise ValueError("Le champ de commandes doit etre du texte.")
    if len(command_text) > MAX_COMMAND_TEXT_LENGTH:
        raise ValueError(
            f"Les commandes depassent la limite de {MAX_COMMAND_TEXT_LENGTH} caracteres."
        )

    commands = []
    for line_number, raw_line in enumerate(command_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError as exc:
            _validation_error(line_number, f"syntaxe invalide ({exc}).")

        if len(parts) < 4 or parts[:2] != ["docker", "exec"]:
            _validation_error(
                line_number,
                "format attendu: docker exec [--user www-data] nextcloud php occ <commande>.",
            )

        index = 2
        user = "www-data"
        while index < len(parts) and parts[index].startswith("-"):
            option = parts[index]
            if option in _TTY_OPTIONS:
                index += 1
                continue
            if option in {"-u", "--user"}:
                if index + 1 >= len(parts):
                    _validation_error(line_number, "utilisateur Docker manquant.")
                user = parts[index + 1]
                index += 2
                continue
            if option.startswith("--user="):
                user = option.split("=", 1)[1]
                index += 1
                continue
            _validation_error(line_number, f"option docker exec interdite: {option}.")

        if user not in _ALLOWED_USERS:
            _validation_error(
                line_number,
                "seuls les utilisateurs Nextcloud www-data, 33 et 82 sont autorises.",
            )
        if index >= len(parts) or parts[index] != NEXTCLOUD_CONTAINER_NAME:
            _validation_error(
                line_number,
                f"le conteneur cible doit etre exactement {NEXTCLOUD_CONTAINER_NAME}.",
            )

        argv = parts[index + 1 :]
        if not argv:
            _validation_error(line_number, "commande OCC manquante.")

        executable = str(PurePosixPath(argv[0]))
        allowed_occ_paths = {"occ", "./occ", "/var/www/html/occ"}
        if executable in allowed_occ_paths:
            occ_index = 0
        elif (
            executable == "php"
            and len(argv) >= 2
            and str(PurePosixPath(argv[1])) in allowed_occ_paths
        ):
            occ_index = 1
        else:
            _validation_error(
                line_number,
                "seules les commandes occ ou php occ sont autorisees.",
            )
        if len(argv) <= occ_index + 1:
            _validation_error(line_number, "sous-commande OCC manquante.")
        if len(argv) > MAX_ARGUMENTS:
            _validation_error(
                line_number, f"plus de {MAX_ARGUMENTS} arguments ne sont pas autorises."
            )

        for argument in argv:
            if len(argument) > MAX_ARGUMENT_LENGTH:
                _validation_error(line_number, "un argument est trop long.")
            if (
                argument in _FORBIDDEN_TOKENS
                or "`" in argument
                or "$(" in argument
                or "${" in argument
                or "\x00" in argument
            ):
                _validation_error(
                    line_number, "les operateurs et substitutions shell sont interdits."
                )

        commands.append(NextcloudExecCommand(tuple(argv), user))
        if len(commands) > MAX_COMMANDS:
            raise ValueError(f"Un maximum de {MAX_COMMANDS} commandes est autorise.")

    return commands
