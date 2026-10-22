"""Create a private local .env without changing the database or existing settings."""
import argparse
import getpass
import os
from pathlib import Path
import secrets
import sys

from dotenv import dotenv_values, set_key
from app.supabase_auth import AuthUnavailable, validate_public_key

REQUIRED = ('DATABASE_URL', 'SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_KEY')
PROMPTS = {
    'DATABASE_URL': 'Supabase PostgreSQL session-pooler connection string',
    'SUPABASE_URL': 'Supabase Project URL (https://<project-ref>.supabase.co)',
    'SUPABASE_ANON_KEY': 'Supabase publishable or legacy anon/public key',
    'SUPABASE_KEY': 'Supabase server secret/service-role key for private Storage',
}


def prepare_env(root, *, interactive):
    path = root / '.env'
    if path.is_symlink():
        raise ValueError('The .env path is a symbolic link. Configure its target explicitly instead.')
    if not path.exists():
        content = (root / '.env.example').read_text()
        with open(path, 'x', encoding='utf-8', opener=lambda name, flags: os.open(name, flags, 0o600)) as destination:
            destination.write(content)
    if os.name != 'nt':
        path.chmod(0o600)
    values = dotenv_values(path, interpolate=False)

    def available(name):
        # Match the app: exported values take precedence over .env entries.
        return bool(os.environ.get(name, values.get(name) or '').strip())

    if not (os.environ.get('SECRET_KEY', '').strip() or (values.get('SECRET_KEY') or '').strip()):
        value = secrets.token_hex(32)
        set_key(path, 'SECRET_KEY', value)
        values['SECRET_KEY'] = value

    missing = [] if available('SECRET_KEY') else ['SECRET_KEY']
    for name in REQUIRED:
        if available(name):
            continue
        if name in os.environ:
            missing.append(name)
            continue  # A blank export overrides .env; it must be unset in the parent shell.
        if interactive:
            prompt = PROMPTS[name] + ': '
            value = (input(prompt) if name == 'SUPABASE_URL' else getpass.getpass(prompt)).strip()
            if value:
                set_key(path, name, value)
                values[name] = value
        if not available(name):
            missing.append(name)
    return path, missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-input', action='store_true', help='Create .env and report missing names without prompting.')
    args = parser.parse_args(argv)
    try:
        path, missing = prepare_env(Path(__file__).resolve().parent,
                                    interactive=not args.no_input and sys.stdin.isatty())
    except (OSError, ValueError, EOFError):
        print('Could not finish .env setup. Check file permissions and rerun in your terminal. No credentials were printed.', file=sys.stderr)
        return 1
    print(f'Local configuration prepared: {path.name}. Existing values were preserved; credentials were not printed.')
    print('Inherited environment credentials remain in the environment; they are not copied into this file.')
    if missing:
        print('Still required: ' + ', '.join(missing))
        print('Unset any blank exported variables first; they override values in .env.')
        print('Run python setup_env.py in an interactive terminal to enter them, or edit your private .env.')
        return 1
    values = dotenv_values(path, interpolate=False)
    try:
        validate_public_key(os.environ.get('SUPABASE_ANON_KEY', values.get('SUPABASE_ANON_KEY') or '').strip())
    except AuthUnavailable as error:
        print(str(error), file=sys.stderr)
        print('Correct SUPABASE_ANON_KEY in your environment settings or .env. Exported values take precedence.', file=sys.stderr)
        return 1
    print('Required settings are present. Run python run.py to start the app.')
    print('This does not verify credentials, apply migrations, or create Supabase users. See docs/supabase-authentication.md.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
