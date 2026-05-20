# Steam Tinder

A multiplayer web app for friend groups to find what game to play together. Enter everyone's Steam profiles, then each player swipes through your shared library — games that everyone likes bubble up as matches.

## How it works

1. One person creates a room and enters all the Steam profiles (IDs, vanity URLs, or profile links)
2. The app finds every game owned by all players
3. Each player joins the room on their own device and claims their profile
4. Everyone swipes yes/no on games independently
5. When all players finish, the app reveals games that got a unanimous yes

## Setup

**Requirements:** Python 3.8+, a [Steam Web API key](https://steamcommunity.com/dev/apikey)

```bash
git clone <repo>
cd steam_tinder
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```
STEAM_API_KEY=your_key_here
```

Run the app:

```bash
python app.py
```

Open `http://localhost:5000` in a browser.

## Notes

- Players' Steam libraries must be set to **Public** for the app to read them
- Up to 60 shared games are shown, sorted by combined playtime
- Room state is in-memory only — rooms are lost on server restart
