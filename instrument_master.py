import requests

url = "http://directlink.icicidirect.com/NewSecurityMaster/SecurityMaster.zip"

r = requests.get(url, timeout=30)
r.raise_for_status()

with open("SecurityMaster.zip", "wb") as f:
    f.write(r.content)

print("Downloaded SecurityMaster.zip")