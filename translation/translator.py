import threading
from typing import Callable, Optional


class ArgosTranslator:
    """Offline text translation using argostranslate."""

    def __init__(self):
        self._installed_pairs: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def ensure_package(
        self,
        from_code: str,
        to_code: str,
        on_status: Optional[Callable[[str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Download and install a language package if needed. Returns True on success."""
        import argostranslate.package
        import argostranslate.translate

        log = on_log or (lambda s: None)
        pair = (from_code, to_code)

        with self._lock:
            if pair in self._installed_pairs:
                return True

        # Check if already installed
        installed = argostranslate.translate.get_installed_languages()
        from_langs = [lang for lang in installed if lang.code == from_code]
        if from_langs:
            translations = from_langs[0].get_translations()
            if any(t.to_language.code == to_code for t in translations):
                with self._lock:
                    self._installed_pairs.add(pair)
                return True

        # Need to download
        log(f"Descargando paquete de traduccion {from_code} -> {to_code}...")
        if on_status:
            on_status(f"Descargando traductor {from_code}->{to_code}...")

        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        pkg = next(
            (p for p in available if p.from_code == from_code and p.to_code == to_code),
            None,
        )
        if pkg is None:
            log(f"Paquete de traduccion {from_code}->{to_code} no disponible.")
            return False

        log(f"Instalando paquete {from_code}->{to_code} (~100 MB)...")
        if on_status:
            on_status(f"Instalando traductor {from_code}->{to_code}...")
        argostranslate.package.install_from_path(pkg.download())

        with self._lock:
            self._installed_pairs.add(pair)
        log(f"Paquete {from_code}->{to_code} instalado correctamente.")
        return True

    def translate(self, text: str, from_code: str, to_code: str) -> str:
        """Translate text. Package must be installed first via ensure_package."""
        import argostranslate.translate

        return argostranslate.translate.translate(text, from_code, to_code)
