import subprocess
import time
import random

# ──────────────────────────────────────────────
# ExpressVPN Control Section
# ──────────────────────────────────────────────

EXPRESSVPN_CLI = r"C:\Program Files (x86)\ExpressVPN\services\ExpressVPN.CLI.exe"


def run_expressvpn_cmd(args, timeout=90):
    """Run ExpressVPN.CLI.exe command"""
    try:
        result = subprocess.run(
            [EXPRESSVPN_CLI] + args,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout
        )
        output = result.stdout.strip()
        if output:
            print(f"ExpressVPN CLI: {output}")
        return True, output
    except Exception as e:
        print(f"ExpressVPN CLI error: {e}")
        return False, str(e)


def vpn_connect(location_id=None):
    """Connect to VPN - None = Smart Location"""
    args = ["connect"]
    if location_id is not None:
        args.append(str(location_id))
    success, _ = run_expressvpn_cmd(args)
    if success:
        time.sleep(8 + random.uniform(0, 5))
    return success


def vpn_disconnect():
    """Disconnect from VPN"""
    success, _ = run_expressvpn_cmd(["disconnect"])
    if success:
        time.sleep(4 + random.uniform(0, 3))
    return success


def change_vpn_location(location_id):
    """
    Disconnect → wait → connect to new location (with retry logic)
    """
    attempt = 0
    changed = False

    while not changed:
        print(f"Disconnecting VPN (attempt {attempt + 1})")
        vpn_disconnect()
        print("Zzzzzz")
        time.sleep(4 + random.uniform(0, 3))

        print(f"Changing VPN to ID: {location_id}")
        changed = vpn_connect(location_id)

        attempt += 1
        if attempt > 10:
            print("Failed after 10 attempts")
            return False

    print("Location changed successfully")
    return True


def select_random_location():
    """Select random location using numeric IDs from your list output"""
    location_map = {
        "ukw1": 90,  # UK - Wembley
        "ukdo": 53,  # UK - Docklands
        "nlams": 4,  # Netherlands - Amsterdam
        "frpa": 8,  # France - Paris - 2
        "defr": 7,  # Germany - Frankfurt - 1
    }

    alias = random.choice(list(location_map.keys()))
    location_id = location_map[alias]

    print(f"Selected location: {alias} (ID: {location_id})")

    success = change_vpn_location(location_id)
    if success:
        print("IP rotation successful → continuing...")
    else:
        print("IP rotation failed → pausing longer...")
        time.sleep(30)


def reconnect():
    """Reconnect to Smart Location (best available)"""
    print("Reconnecting to Smart Location...")
    vpn_disconnect()
    time.sleep(6)
    success = vpn_connect()  # no argument = Smart Location
    if success:
        print("Reconnected successfully")
    else:
        print("Reconnect failed")


if __name__ == "__main__":
    print("Testing VPN rotation...")
    select_random_location()
    select_random_location()
    select_random_location()
    # You can also test reconnect if you want:
    # time.sleep(15)
    # reconnect()


