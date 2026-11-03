"""Create a private admin environment file; never print credentials or overwrite a file."""
import argparse
import getpass
import os
from pathlib import Path
import secrets
import shlex

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description='Initialiser un administrateur Nova Atlas')
    parser.add_argument('--output', type=Path, default=Path('.env.admin'))
    args = parser.parse_args()
    username = input('Identifiant administrateur : ').strip()
    if not username or len(username) > 128 or any(c in username for c in '\r\n\x00'):
        parser.error('Identifiant invalide')
    password = getpass.getpass('Mot de passe (16 caractères minimum) : ')
    confirmation = getpass.getpass('Confirmer le mot de passe : ')
    if len(password) < 16 or len(password) > 1024 or password != confirmation:
        parser.error('Mot de passe trop court/long ou confirmation différente')
    values = {'ATLAS_ADMIN_USERNAME': username,
              'ATLAS_ADMIN_PASSWORD_HASH': generate_password_hash(password, method='scrypt:32768:8:1'),
              'ATLAS_SESSION_SECRET': secrets.token_hex(32),
              'ATLAS_COOKIE_SECURE': '1'}
    content = '# Private shell environment. Never commit or log this file.\n'
    content += ''.join(f'{key}={shlex.quote(value)}\n' for key, value in values.items())
    try:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error('Le fichier existe déjà ; aucune écriture effectuée')
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)
    print('Fichier privé créé (permissions 0600). Aucun secret affiché.')


if __name__ == '__main__':
    main()
