import time
import requests


class Scryfall:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "BuscadorCartas/1.0", "Accept": "application/json"})
        self.last_request = 0

    def get(self, url, **kwargs):
        time.sleep(max(0, 0.1 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            response = self.session.get(url, timeout=15, **kwargs)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            raise RuntimeError(f"No se pudo consultar Scryfall: {error}") from error

    def set_codes(self):
        response = self.get("https://api.scryfall.com/sets")
        if response is None:
            raise RuntimeError("No se pudieron obtener las ediciones de Scryfall")
        return {item["code"] for item in response.json()["data"]}

    @staticmethod
    def query(edition, number, foil):
        return f"set:{edition} cn:{number} lang:en is:{'foil' if foil else 'nonfoil'}"

    def search(self, query):
        response = self.get("https://api.scryfall.com/cards/search", params={"q": query})
        return response.json()["data"][0] if response is not None else None

    def image(self, url):
        response = self.get(url)
        if response is None:
            raise RuntimeError("Imagen no disponible")
        return response.content
