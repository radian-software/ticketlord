import configparser
import os
import pathlib
import subprocess
import tempfile

with tempfile.TemporaryDirectory() as tempdir:
    print(tempdir)
    home_dir = pathlib.Path(tempdir) / "home"
    profile_name = "ticketlord"
    subprocess.run(
        ["firefox", "--createprofile", profile_name],
        check=True,
        env={**os.environ, "HOME": str(home_dir)},
    )
    firefox_dir = home_dir / ".mozilla" / "firefox"
    profiles_ini = firefox_dir / "profiles.ini"
    with open(profiles_ini) as f:
        config = configparser.ConfigParser()
        config.read_file(f)
        profile_dir = firefox_dir / config["Profile0"]["path"]
    prefs_js = profile_dir / "prefs.js"
    with open(prefs_js, "w") as f:
        f.write('user_pref("browser.toolbars.bookmarks.visibility", "never");\n')
    subprocess.run(
        ["firefox", "-P", profile_name],
        check=True,
        env={**os.environ, "HOME": str(home_dir)},
    )
