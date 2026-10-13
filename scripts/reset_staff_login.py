"""Local-owner recovery for the single staff account. Password input is hidden."""
from getpass import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from biometrics import Store,FaceError
from parent_portal import PortalAccess
from werkzeug.security import generate_password_hash


def main():
    root=Path(__file__).resolve().parent.parent
    access=PortalAccess(Store(root/'data'/'identities.sqlite3'))
    login_id=access.valid_login_id(input('New staff login ID: '))
    password=access.valid_password(getpass('New password (10–128 characters): '))
    if password!=getpass('Confirm password: '):
        raise FaceError('password_mismatch','Passwords must match. No credentials changed.')
    with access.store.lock,access.store.connect() as db:
        db.execute('UPDATE staff_credentials SET login_id=?,password_hash=? WHERE id=1',(login_id,generate_password_hash(password)))
        db.execute("DELETE FROM portal_sessions WHERE role='staff'")
    access.staff_login_path.unlink(missing_ok=True)
    print('Staff login updated. All staff sessions signed out.')

if __name__=='__main__':
    try:main()
    except (FaceError,EOFError,KeyboardInterrupt) as error:
        print(str(error) or 'Cancelled.',file=sys.stderr);sys.exit(1)
