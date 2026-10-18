"""Bootstrap de embalagem. O código de engenharia permanece no ZIP original."""
from __future__ import annotations

import builtins
from contextlib import contextmanager
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import threading
import time
import types
import unicodedata
import uuid
import zipfile

SOURCE_SHA256 = "68de90199398c98c947c2358b7e36a1a29ec4bbdbe383b7cb483e228abce462a"
SOURCE_BYTES = 4248609
PAYLOAD_FOLDER = "LRO_MONOVIA_030_20261018"
MAX_TOTAL_BYTES = 128 * 1024 * 1024
_STATE_NAME = "_lro_monovia_030_bootstrap_state"
_RECEIPT = ".bootstrap_receipt.json"


class BootstrapError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _no_links(path: Path) -> None:
    """Also reject Windows junctions/reparse points, including parent paths."""
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise BootstrapError(f"Link ou ponto de redirecionamento não permitido: {item}")


def _member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name or ":" in name:
        raise BootstrapError(f"Caminho inseguro no ZIP: {name!r}")
    parts = name.rstrip("/").split("/")
    reserved = {"con", "prn", "aux", "nul", "conin$", "conout$",
                *(f"com{i}" for i in (*range(1, 10), "¹", "²", "³")),
                *(f"lpt{i}" for i in (*range(1, 10), "¹", "²", "³"))}
    if any(not p or p in (".", "..") or p[-1] in " ." or
           any(ord(c) < 32 or c in '<>"|?*' for c in p) or
           p.split(".")[0].casefold() in reserved for p in parts):
        raise BootstrapError(f"Caminho inseguro no ZIP: {name!r}")
    result = PurePosixPath(*parts)
    if result.is_absolute() or parts[0] != PAYLOAD_FOLDER:
        raise BootstrapError(f"Raiz inesperada no ZIP: {name!r}")
    return result


def _archive_index(archive: zipfile.ZipFile) -> dict[str, dict]:
    infos = archive.infolist()
    if len(infos) > 2000:
        raise BootstrapError("Quantidade de arquivos excede o limite da embalagem.")
    seen: dict[str, bool] = {}
    files: dict[str, dict] = {}
    total = 0
    for info in infos:
        path = _member_path(info.filename)
        normalized = unicodedata.normalize("NFC", str(path)).casefold()
        if normalized in seen:
            raise BootstrapError(f"Caminho duplicado no ZIP: {info.filename}")
        seen[normalized] = info.is_dir()
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
            raise BootstrapError(f"Tipo de entrada não permitido: {info.filename}")
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_TOTAL_BYTES or info.file_size > MAX_TOTAL_BYTES:
            raise BootstrapError("Tamanho descompactado excede o limite da embalagem.")
        # Reading to EOF verifies the ZIP CRC before any extraction is published.
        digest = hashlib.sha256()
        with archive.open(info) as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        files[str(path)] = {"sha256": digest.hexdigest(), "bytes": info.file_size}
    for name in seen:
        for parent in PurePosixPath(name).parents:
            if str(parent) in seen and not seen[str(parent)]:
                raise BootstrapError("Conflito entre arquivo e diretório no ZIP.")
    manifest_name = f"{PAYLOAD_FOLDER}/RUNTIME_MANIFEST.json"
    if manifest_name not in files:
        raise BootstrapError("Manifesto original ausente.")
    manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
    declared = manifest.get("files", {})
    expected = {f"{PAYLOAD_FOLDER}/{name}": value for name, value in declared.items()}
    actual = {name: value for name, value in files.items() if name != manifest_name}
    if manifest.get("version") != "0.3.0" or len(files) != 290 or len(declared) != 289 or expected != actual:
        raise BootstrapError("Payload não confere com o manifesto original 0.3.0.")
    for entry in ("app.py", "pages/2_Complemento_EN.py", "pages/3_Fadiga_NBR.py"):
        if f"{PAYLOAD_FOLDER}/{entry}" not in files:
            raise BootstrapError(f"Página original ausente: {entry}")
    return files


def _tree_matches(root: Path, index: dict[str, dict], *, receipt: bool) -> bool:
    try:
        _no_links(root)
        found: set[str] = set()
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in (*dirs, *files):
                _no_links(Path(folder) / name)
            for name in files:
                path = Path(folder) / name
                relative = path.relative_to(root).as_posix()
                if receipt and relative == _RECEIPT:
                    continue
                if relative not in index or path.stat().st_size != index[relative]["bytes"]:
                    return False
                if _sha(path) != index[relative]["sha256"]:
                    return False
                found.add(relative)
        return found == set(index)
    except (OSError, BootstrapError):
        return False


def _cache_matches(root: Path, index: dict[str, dict], digest: str) -> bool:
    if not _tree_matches(root, index, receipt=True):
        return False
    try:
        receipt = json.loads((root / _RECEIPT).read_text(encoding="utf-8"))
        return receipt == {"archive_sha256": digest, "files": index}
    except (OSError, ValueError):
        return False


@contextmanager
def _cache_publication_lock(cache_base: Path, digest: str):
    """Serialize cache transitions across threads/processes; close/crash unlocks.

    Keep the lock file: unlinking it could let two processes lock different
    inodes under the same name. Its contents do not represent lock ownership.
    """
    with _process_state().lock:
        lock_path = cache_base / f".{digest}.lock"
        _no_links(lock_path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or
                    getattr(info, "st_file_attributes", 0) & 0x400):
                raise BootstrapError("Arquivo de lock do cache inseguro.")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                def acquire():
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)

                contention = (errno.EACCES, errno.EAGAIN, errno.EDEADLK)
            else:
                import fcntl

                def acquire():
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

                contention = (errno.EACCES, errno.EAGAIN)
            deadline = time.monotonic() + 120
            while True:
                try:
                    acquire()
                    break
                except OSError as exc:
                    if exc.errno not in contention:
                        raise
                    if time.monotonic() >= deadline:
                        raise BootstrapError("Outro processo mantém o cache ocupado; tente iniciar novamente.") from exc
                    time.sleep(0.05)
            # Byte-range locks may extend past EOF. Initialize only while
            # owning the lock: an earlier writer could already hold byte 0.
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            yield
        finally:
            # OS file locks are released by closing this non-inherited fd,
            # including when extraction raises or the owning process exits.
            os.close(descriptor)


def prepare_archive(archive_path: Path, cache_base: Path) -> tuple[Path, str, dict]:
    """Verify before opening, stage safely, and reuse only a fully verified cache."""
    _no_links(archive_path)
    archive_path = archive_path.resolve(strict=True)
    archive_bytes = archive_path.read_bytes()
    digest = hashlib.sha256(archive_bytes).hexdigest()
    if digest != SOURCE_SHA256 or len(archive_bytes) != SOURCE_BYTES:
        raise BootstrapError("codigo_lro.zip não corresponde à fonte oficial Monovia 0.3. "
                             "Restaure o ZIP desta entrega; nenhum código interno foi executado.")
    _no_links(cache_base)
    cache_base.mkdir(parents=True, exist_ok=True)
    _no_links(cache_base)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        index = _archive_index(archive)
        with _cache_publication_lock(cache_base, digest):
            return _publish_cache(archive, cache_base, digest, index)


def _publish_cache(archive, cache_base: Path, digest: str, index: dict):
    """Caller holds the per-digest OS lock through verification and publication."""
    target = cache_base / digest
    _no_links(target)
    if target.exists():
        if _cache_matches(target, index, digest):
            return target, digest, index
        # Quarantine only this owned hash directory; never recursively delete it.
        target.rename(cache_base / f".invalid-{digest}-{uuid.uuid4().hex}")
    staging = Path(tempfile.mkdtemp(prefix=".stage-", dir=cache_base))
    for info in archive.infolist():
        relative = _member_path(info.filename)
        destination = staging.joinpath(*relative.parts)
        if not destination.resolve().is_relative_to(staging.resolve()):
            raise BootstrapError("Tentativa de escape durante extração.")
        if info.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)
    if not _tree_matches(staging, index, receipt=False):
        raise BootstrapError("A verificação dos arquivos extraídos falhou.")
    (staging / _RECEIPT).write_text(json.dumps({"archive_sha256": digest, "files": index},
                                             ensure_ascii=False, sort_keys=True), encoding="utf-8")
    try:
        staging.rename(target)
    except OSError as exc:
        # POSIX may report ENOTEMPTY for a directory published by a peer;
        # Windows commonly reports EEXIST. Never swallow unrelated errors.
        if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
            raise
        if not _cache_matches(target, index, digest):
            raise BootstrapError("Cache concorrente inválido; reinicie o aplicativo.") from exc
    return target, digest, index


def _process_state():
    state = sys.modules.get(_STATE_NAME)
    if state is None:
        state = types.ModuleType(_STATE_NAME)
        state.lock = threading.RLock()
        state.archive_hash = None
        state.installation = None
        state.runtime = None
        state.index = None
        state = sys.modules.setdefault(_STATE_NAME, state)
    return state


def bootstrap_runtime(installation: Path) -> Path:
    _no_links(installation.absolute())
    installation = installation.resolve(strict=True)
    archive_path = installation / "codigo_lro.zip"
    state = _process_state()
    with state.lock:
        current_hash = _sha(archive_path)
        if state.archive_hash is not None:
            if state.installation != installation or current_hash != state.archive_hash:
                raise BootstrapError("O pacote mudou durante esta execução. Encerre o servidor Streamlit "
                                     "(Ctrl+C) e inicie novamente. Recálculo e exportação foram bloqueados "
                                     "para impedir mistura de versões em memória.")
            if not _tree_matches(state.runtime.parent, state.index, receipt=False):
                raise BootstrapError("Os arquivos do runtime foram alterados. Encerre e reinicie o servidor "
                                     "para criar uma extração íntegra; os resultados atuais foram bloqueados.")
            return state.runtime
        base = installation / ".monovia_runtime"
        _no_links(base)
        cache, digest, index = prepare_archive(archive_path, base / "cache")
        # Derive every importable root from the verified archive, rather than
        # keeping a partial list that could miss a stale EN/fatigue dependency.
        import_roots = set()
        for name in index:
            parts = PurePosixPath(name).parts[1:]
            if name.endswith(".py"):
                import_roots.add(parts[0] if len(parts) > 1 else PurePosixPath(parts[0]).stem)
        import_roots -= {"app", "__init__"}
        if any(name == prefix or name.startswith(prefix + ".") for name in tuple(sys.modules)
               for prefix in import_roots):
            raise BootstrapError("Já há módulos Monovia de outra execução neste processo. "
                                 "Reinicie o servidor antes de abrir esta embalagem.")
        runs = base / "runs"
        _no_links(runs)
        runs.mkdir(parents=True, exist_ok=True)
        run_root = Path(tempfile.mkdtemp(prefix=f"{digest}-", dir=runs))
        for name in index:
            source = cache.joinpath(*PurePosixPath(name).parts)
            destination = run_root.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        if not _tree_matches(run_root, index, receipt=False):
            raise BootstrapError("A cópia isolada de execução não confere com a fonte oficial.")
        runtime = run_root / PAYLOAD_FOLDER
        entrypoint = runtime / "app.py"
        if entrypoint.resolve() == (installation / "app.py").resolve():
            raise BootstrapError("Bootstrap recursivo recusado.")
        state.installation, state.archive_hash = installation, digest
        state.runtime, state.index = runtime, index
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(runtime))
        # The original modules have physical relative reads; cwd is fixed per process.
        os.chdir(runtime)
        return runtime


def main() -> None:
    import streamlit as st
    try:
        runtime = bootstrap_runtime(Path(__file__).absolute().parent)
    except (BootstrapError, OSError, zipfile.BadZipFile, ValueError) as exc:
        st.error(f"Não foi possível iniciar Monovia 0.3: {exc}")
        st.stop()
        return
    def initial_nbr():
        # Adapt only the original script's Streamlit import. Never patch global
        # Streamlit functions, shared modules, scientific code or source files.
        proxy = types.ModuleType("streamlit")
        proxy.__getattr__ = lambda name: getattr(st, name)

        def physical_page_link(page, *args, **kwargs):
            if isinstance(page, (str, Path)):
                page = {"pages/2_Complemento_EN.py": en,
                        "pages/3_Fadiga_NBR.py": fatigue}.get(str(page).replace("\\", "/"), page)
            return st.page_link(page, *args, **kwargs)

        proxy.page_link = physical_page_link
        imports = dict(vars(builtins))
        original_import = builtins.__import__

        def import_for_initial(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "streamlit" and level == 0:
                return proxy
            return original_import(name, globals, locals, fromlist, level)

        imports["__import__"] = import_for_initial
        original = runtime / "app.py"
        namespace = {"__name__": "__main__", "__file__": str(original),
                     "__package__": None, "__builtins__": imports}
        exec(compile(original.read_bytes(), str(original), "exec"), namespace)

    initial = st.Page(initial_nbr, title="NBR inicial", url_path="nbr", default=True)
    en = st.Page(str(runtime / "pages" / "2_Complemento_EN.py"), title="Complemento EN", url_path="en")
    fatigue = st.Page(str(runtime / "pages" / "3_Fadiga_NBR.py"), title="Fadiga NBR", url_path="fadiga-nbr")
    st.navigation([initial, en, fatigue]).run()


if __name__ == "__main__":
    main()
