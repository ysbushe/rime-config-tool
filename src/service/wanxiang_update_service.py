"""Safe Wanxiang Base/full-pinyin updater with capability-first checks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import urllib.request
import zipfile

import yaml

from src import __version__
from src.config.paths import user_config_dir
from src.service.backup_service import BackupService
from src.service.file_transaction import FileChange, TransactionalFileWriter, capture_revision


OFFICIAL_REPOSITORY = "amzxyz/rime_wanxiang"
MAX_VERIFIED_VERSION = "17.2.4"
_API = f"https://api.github.com/repos/{OFFICIAL_REPOSITORY}/releases/latest"
_RULES_URL = (
    "https://raw.githubusercontent.com/ysbushe/rime-config-tool/"
    "main/wanxiang-compatibility.json"
)
_PROTECTED_PARTS = {"sync", "userdb", ".git"}
_PROTECTED_NAMES = {
    "user.yaml", "installation.yaml", "custom_phrase.txt",
    "wanxiang_user_phrase.txt", "pinyin_display.ini",
}
_REQUIRED_PACKAGE_FILES = {
    "version.txt",
    "wanxiang.schema.yaml",
    "wanxiang.dict.yaml",
    "wanxiang_algebra.yaml",
    "lua/wanxiang/super_english.lua",
    "lua/wanxiang/super_lookup.lua",
}
_RECOVERY_EXTRA_NAMES = {
    "user.yaml", "installation.yaml", "custom_phrase.txt",
    "wanxiang_user_phrase.txt", "pinyin_display.ini", "weasel.custom.yaml",
}


@dataclass(frozen=True)
class WanxiangUpdateReport:
    current_version: str
    new_version: str
    tool_version: str
    compatible: bool
    reason: str
    download_url: str = ""
    waiting_for_adapter: bool = False
    preflight_passed: bool = False
    requires_confirmation: bool = False
    compatibility_source: str = ""
    force_available: bool = False


def _version(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(item) for item in numbers[:4])


class WanxiangUpdateService:
    def __init__(self, rime_dir: str, backup: BackupService,
                 writer: TransactionalFileWriter) -> None:
        self.root = Path(rime_dir)
        self.backup = backup
        self.writer = writer
        self.state_dir = user_config_dir() / "wanxiang-update"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir = self.state_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.recovery_dir = self.state_dir / "recovery"
        self.manifest_path = self.state_dir / "baseline.json"
        self.diagnosis_path = self.state_dir / "last-check.json"
        self.rules_cache_path = self.state_dir / "compatibility-rules.json"
        self.rollback_path = self.state_dir / "last-update-snapshot.json"

    def current_version(self) -> str:
        version_file = self.root / "version.txt"
        try:
            value = version_file.read_text(encoding="utf-8-sig").strip()
            if _version(value):
                return value
        except (OSError, UnicodeError):
            pass
        path = self.root / "wanxiang.schema.yaml"
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
            return str((value.get("schema") or {}).get("version", ""))
        except Exception:
            return ""

    def check(self) -> WanxiangUpdateReport:
        """Check metadata first, then inspect every newer package before allowing it."""
        current = self.current_version()
        try:
            release = self._read_json_url(_API, timeout=15)
        except Exception as exc:
            return self._record(WanxiangUpdateReport(
                current, "", __version__, False, f"检查万象更新失败：{exc}",
            ))
        tag = str(release.get("tag_name", "")).lstrip("vV")
        assets = release.get("assets") or []
        asset = next((
            item for item in assets
            if str(item.get("name", "")).casefold() == "rime-wanxiang-base.zip"
            and str(item.get("browser_download_url", "")).lower().endswith(".zip")
        ), None)
        url = str((asset or {}).get("browser_download_url", ""))
        if not tag or not _version(tag):
            return self._record(WanxiangUpdateReport(
                current, tag, __version__, False, "官方版本格式无法识别", url, True,
            ))
        if not self._trusted_download_url(url):
            return self._record(WanxiangUpdateReport(
                current, tag, __version__, False, "官方更新包下载方式发生变化", url, True,
            ))
        if current and _version(tag) <= _version(current):
            return self._record(WanxiangUpdateReport(
                current, tag, __version__, True, "当前万象已是最新版本", url,
                preflight_passed=True, compatibility_source="已安装版本",
            ))

        try:
            archive = self._obtain_archive(tag, url)
            files, layout = self._inspect(archive)
            self._validate_capabilities(tag, files, layout)
        except Exception as exc:
            return self._record(WanxiangUpdateReport(
                current, tag, __version__, False,
                f"新版结构或配置能力预检未通过：{exc}", url, True,
            ))

        rules, source = self._load_compatibility_rules()
        blocked = rules.get("blocked_versions") or {}
        blocked_reason = blocked.get(tag) if isinstance(blocked, dict) else None
        if blocked_reason:
            return self._record(WanxiangUpdateReport(
                current, tag, __version__, False,
                f"在线适配规则已暂停该版本：{blocked_reason}", url, True,
                preflight_passed=True, compatibility_source=source,
            ))
        verified = str(rules.get("verified_through") or MAX_VERIFIED_VERSION)
        if _version(tag) <= _version(verified):
            reason = f"已通过结构预检，并位于已验证范围 {verified} 内"
            return self._record(WanxiangUpdateReport(
                current, tag, __version__, True, reason, url,
                preflight_passed=True, compatibility_source=source,
            ))
        return self._record(WanxiangUpdateReport(
            current, tag, __version__, True,
            "新版尚未登记，但文件结构和现有设置能力检查通过，可谨慎更新",
            url, False, True, True, source,
        ))

    def update(self, report: WanxiangUpdateReport, *, force: bool = False) -> tuple[bool, str]:
        """Install a preflighted package; force only bypasses local-file guards."""
        if not report.download_url or not self._trusted_download_url(report.download_url):
            return False, report.reason or "更新包来源不安全"
        if not report.preflight_passed and not report.compatible:
            return False, report.reason
        archive = self._archive_path(report.new_version)
        partial = archive.with_suffix(".zip.part")
        try:
            if not archive.is_file():
                self._download_archive(report.download_url, partial)
                partial.replace(archive)
            files, layout = self._inspect(archive)
            self._validate_capabilities(report.new_version, files, layout)
            baseline = self._load_manifest()
            if not baseline and not force:
                self._save_manifest(report.current_version, files, layout, archive, adopt=True)
                return False, "已记录当前万象安全基线，请再次点击“安装更新”继续。"

            changed = self._locally_changed_files(files, baseline)
            if changed and not force:
                return False, (
                    "需要强制更新：检测到官方文件曾被本地修改，普通更新没有覆盖："
                    + "、".join(changed[:8])
                )

            self._create_recovery_backup(
                report.current_version, report.new_version, files,
                force=force, archive=archive,
            )
            changes = []
            for relative, payload in files.items():
                target = self.root / relative
                try:
                    unchanged = target.is_file() and target.read_bytes() == payload
                except OSError:
                    unchanged = False
                if unchanged:
                    continue
                changes.append(FileChange(target, payload, capture_revision(target)))
            if changes:
                self.writer.commit(changes)
            self._save_manifest(report.new_version, files, layout, archive, adopt=False)
            mode = "强制" if force else "安全"
            return True, (
                f"万象已{mode}更新至 {report.new_version}；"
                f"实际写入 {len(changes)} 个变更文件，个人词库和自定义配置未改动；"
                "正在等待重新部署。"
            )
        except Exception as exc:
            return False, f"万象更新预检或写入失败：{exc}"
        finally:
            partial.unlink(missing_ok=True)

    def confirm_update(self) -> tuple[bool, str]:
        """Rotate pending recovery data only after deployment succeeds."""
        try:
            state = self._load_recovery_pointer()
            if state.get("status") != "pending":
                return False, "没有等待确认的万象更新备份。"
            pending = self._safe_recovery_path(state.get("snapshot_dir"))
            current = self.recovery_dir / "current"
            if not pending.is_dir():
                raise RuntimeError("更新恢复备份不存在")
            if current.exists():
                self._remove_recovery_tree(current)
            pending.replace(current)
            state["snapshot_dir"] = str(current)
            state["status"] = "current"
            self._write_recovery_pointer(state)
            (current / "manifest.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            return True, "已保留本次更新前的独立恢复备份。"
        except Exception as exc:
            return False, f"万象更新备份轮换失败：{exc}"

    def validate_deployment(self) -> tuple[bool, str]:
        """Check the concrete Wanxiang build artifacts produced by Weasel."""
        required = (
            self.root / "build" / "default.yaml",
            self.root / "build" / "wanxiang.schema.yaml",
            self.root / "build" / "wanxiang.table.bin",
        )
        missing = []
        for path in required:
            try:
                if not path.is_file() or path.stat().st_size <= 0:
                    missing.append(path.name)
            except OSError:
                missing.append(path.name)
        if missing:
            return False, "未生成完整的万象构建产物：" + "、".join(missing)
        return True, "已生成万象方案与词典构建产物。"

    def has_recovery_backup(self) -> bool:
        try:
            state = self._load_recovery_pointer()
            return self._safe_recovery_path(state.get("snapshot_dir")).is_dir()
        except Exception:
            return False

    def recovery_summary(self) -> str:
        try:
            state = self._load_recovery_pointer()
            if not self._safe_recovery_path(state.get("snapshot_dir")).is_dir():
                return "暂无独立更新备份"
            return (
                f"可恢复：{state.get('source_version') or '未知'} → "
                f"{state.get('target_version') or '未知'} 前，"
                f"{state.get('created_at') or '时间未知'}"
            )
        except Exception:
            return "暂无独立更新备份"

    def rollback_last_update(self) -> tuple[bool, str]:
        """Restore the exact pre-update file set from isolated recovery data."""
        try:
            state = self._load_recovery_pointer()
            snapshot = self._safe_recovery_path(state["snapshot_dir"])
            targets = dict(state.get("targets") or {})
            changes: list[FileChange] = []
            created: list[Path] = []
            for relative, metadata in targets.items():
                existed = bool(metadata.get("existed")) if isinstance(metadata, dict) else bool(metadata)
                target = self.root / Path(*PurePosixPath(relative).parts)
                if existed:
                    source = snapshot / "files" / Path(*PurePosixPath(relative).parts)
                    if not source.is_file():
                        raise RuntimeError(f"恢复备份缺少 {relative}")
                    expected_hash = str(metadata.get("sha256", "")) if isinstance(metadata, dict) else ""
                    payload = source.read_bytes()
                    if expected_hash and self._hash(payload) != expected_hash:
                        raise RuntimeError(f"恢复备份校验失败：{relative}")
                    changes.append(FileChange(
                        target, payload, capture_revision(target),
                    ))
                elif target.exists():
                    created.append(target)
            if changes:
                self.writer.commit(changes)
            for target in created:
                if target.is_file() and self.root.resolve() in target.resolve().parents:
                    target.unlink()
            baseline = snapshot / "baseline.json"
            if baseline.is_file():
                shutil.copy2(baseline, self.manifest_path)
            elif self.manifest_path.exists():
                self.manifest_path.unlink()

            if state.get("status") == "pending":
                self._remove_recovery_tree(snapshot)
                current = self.recovery_dir / "current"
                manifest = current / "manifest.json"
                if manifest.is_file():
                    self.rollback_path.write_bytes(manifest.read_bytes())
                else:
                    self.rollback_path.unlink(missing_ok=True)
            return True, "万象文件已恢复到更新前快照。"
        except Exception as exc:
            return False, f"万象自动回滚失败：{exc}"

    @staticmethod
    def _read_json_url(url: str, *, timeout: int) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": "RimeConfigTool"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise RuntimeError("远程数据格式错误")
        return value

    @staticmethod
    def _trusted_download_url(url: str) -> bool:
        return (
            url.startswith("https://github.com/amzxyz/rime-wanxiang/releases/download/")
            or url.startswith("https://github.com/amzxyz/rime_wanxiang/releases/download/")
        )

    def _load_compatibility_rules(self) -> tuple[dict, str]:
        fallback = {
            "format": 1,
            "verified_through": MAX_VERIFIED_VERSION,
            "blocked_versions": {},
        }
        try:
            remote = self._read_json_url(_RULES_URL, timeout=5)
            if int(remote.get("format", 0)) != 1:
                raise RuntimeError("规则格式版本不支持")
            self.rules_cache_path.write_text(
                json.dumps(remote, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            return remote, "在线适配规则"
        except Exception:
            try:
                cached = json.loads(self.rules_cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and int(cached.get("format", 0)) == 1:
                    return cached, "上次可用的在线适配规则"
            except Exception:
                pass
        return fallback, "程序内置规则"

    def _archive_path(self, version: str) -> Path:
        safe = re.sub(r"[^0-9A-Za-z._-]+", "-", str(version or "unknown"))
        return self.download_dir / f"wanxiang-{safe}.zip"

    def _obtain_archive(self, version: str, url: str) -> Path:
        archive = self._archive_path(version)
        if archive.is_file():
            return archive
        partial = archive.with_suffix(".zip.part")
        try:
            self._download_archive(url, partial)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
        for other in self.download_dir.glob("wanxiang-*.zip"):
            if other != archive:
                other.unlink(missing_ok=True)
        return archive

    @staticmethod
    def _download_archive(url: str, destination: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "RimeConfigTool"})
        with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)

    def _inspect(self, archive: Path) -> tuple[dict[str, bytes], list[str]]:
        selected: dict[str, bytes] = {}
        roots: set[str] = set()
        with zipfile.ZipFile(archive) as package:
            entries = [
                (item, PurePosixPath(item.filename))
                for item in package.infolist() if not item.is_dir()
            ]
            if len(entries) > 5000 or sum(item.file_size for item, _path in entries) > 1024 ** 3:
                raise RuntimeError("更新包体积或文件数量异常")
            schema = [path for _item, path in entries if path.name == "wanxiang.schema.yaml"]
            if len(schema) != 1:
                raise RuntimeError("更新包无法唯一定位 wanxiang.schema.yaml")
            prefix = schema[0].parent
            for item, path in entries:
                try:
                    relative = path.relative_to(prefix)
                except ValueError:
                    continue
                if not relative.parts or self._protected(relative):
                    continue
                if relative.suffix.lower() not in {".yaml", ".lua", ".txt", ".bin"}:
                    continue
                if ".." in relative.parts:
                    raise RuntimeError("更新包包含非法路径")
                if relative.as_posix() in selected:
                    raise RuntimeError(f"更新包包含重复路径：{relative.as_posix()}")
                roots.add(relative.parts[0])
                selected[relative.as_posix()] = package.read(item)
        if "wanxiang.schema.yaml" not in selected or not any(
                name.endswith(".dict.yaml") for name in selected):
            raise RuntimeError("更新包缺少 Base/全拼关键配置")
        return selected, sorted(roots)

    def _validate_capabilities(self, version: str, files: dict[str, bytes],
                               layout: list[str]) -> None:
        missing = sorted(_REQUIRED_PACKAGE_FILES - files.keys())
        if missing:
            raise RuntimeError("缺少关键文件：" + "、".join(missing))
        try:
            schema = yaml.safe_load(files["wanxiang.schema.yaml"].decode("utf-8-sig")) or {}
        except Exception as exc:
            raise RuntimeError(f"主方案无法读取：{exc}") from exc
        if str((schema.get("schema") or {}).get("schema_id", "")) != "wanxiang":
            raise RuntimeError("主方案 ID 不再是 wanxiang")
        required_paths = (
            ("translator", "enable_user_dict"),
            ("translator", "contextual_suggestions"),
            ("custom_phrase", "user_dict"),
            ("wanxiang_english", "english_spacing"),
            ("wanxiang_english", "max_candidates"),
            ("wanxiang_lookup", "enable_tone"),
            ("key_binder", "bindings"),
        )
        absent = ["/".join(path) for path in required_paths if not self._has_path(schema, path)]
        if absent:
            raise RuntimeError("现有可视化设置无法对应：" + "、".join(absent))
        declared = files["version.txt"].decode("utf-8-sig", errors="replace").strip()
        if _version(declared) and _version(version) and _version(declared) != _version(version):
            raise RuntimeError(f"更新包版本 {declared} 与发布版本 {version} 不一致")
        if not layout:
            raise RuntimeError("更新包目录为空")

    @staticmethod
    def _has_path(value: dict, path: tuple[str, ...]) -> bool:
        current = value
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return False
            current = current[key]
        return True

    @staticmethod
    def _protected(path: PurePosixPath) -> bool:
        lower_parts = {part.casefold() for part in path.parts}
        name = path.name.casefold()
        return (
            bool(lower_parts & _PROTECTED_PARTS)
            or name in _PROTECTED_NAMES
            or name.endswith(".custom.yaml")
            or ".userdb" in name
        )

    def _locally_changed_files(self, files: dict[str, bytes], baseline: dict) -> list[str]:
        if not baseline:
            return []
        hashes = baseline.get("hashes") or {}
        changed = []
        for relative in files:
            target = self.root / relative
            old_hash = hashes.get(relative)
            if target.is_file() and (not old_hash or self._hash(target.read_bytes()) != old_hash):
                changed.append(relative)
        return changed

    def _create_recovery_backup(self, source_version: str, target_version: str,
                                files: dict[str, bytes], *, force: bool,
                                archive: Path) -> None:
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        pending = self.recovery_dir / "pending"
        if pending.exists():
            self._remove_recovery_tree(pending)
        (pending / "files").mkdir(parents=True, exist_ok=False)

        relatives = {PurePosixPath(name) for name in files}
        for path in self.root.glob("*.custom.yaml"):
            relatives.add(PurePosixPath(path.name))
        custom_dir = self.root / "custom"
        if custom_dir.is_dir():
            for path in custom_dir.rglob("*.custom.yaml"):
                relatives.add(PurePosixPath(path.relative_to(self.root).as_posix()))
        for name in _RECOVERY_EXTRA_NAMES:
            relatives.add(PurePosixPath(name))
        for pattern in ("*.groups.json", "wanxiang*.txt"):
            for path in self.root.glob(pattern):
                if path.is_file():
                    relatives.add(PurePosixPath(path.name))

        targets: dict[str, dict] = {}
        for relative in sorted(relatives, key=str):
            target = self.root / Path(*relative.parts)
            existed = target.is_file()
            metadata = {"existed": existed, "sha256": ""}
            if existed:
                destination = pending / "files" / Path(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, destination)
                metadata["sha256"] = self._hash(destination.read_bytes())
            targets[relative.as_posix()] = metadata
        if self.manifest_path.is_file():
            shutil.copy2(self.manifest_path, pending / "baseline.json")
        state = {
            "format": 2,
            "status": "pending",
            "snapshot_dir": str(pending),
            "source_version": source_version,
            "target_version": target_version,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "forced": bool(force),
            "archive_sha256": self._hash(archive.read_bytes()),
            "targets": targets,
        }
        (pending / "manifest.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        self._write_recovery_pointer(state)

    def _load_recovery_pointer(self) -> dict:
        value = json.loads(self.rollback_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("恢复记录格式错误")
        return value

    def _write_recovery_pointer(self, value: dict) -> None:
        self.rollback_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _safe_recovery_path(self, value) -> Path:
        path = Path(str(value or "")).resolve()
        root = self.recovery_dir.resolve()
        if path == root or root not in path.parents:
            raise RuntimeError("恢复路径超出万象更新备份目录")
        return path

    def _remove_recovery_tree(self, path: Path) -> None:
        safe = self._safe_recovery_path(path)
        if safe.exists():
            shutil.rmtree(safe)

    def _load_manifest(self) -> dict:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _save_manifest(self, version: str, files: dict[str, bytes], layout: list[str],
                       archive: Path, adopt: bool) -> None:
        hashes = {}
        for relative, payload in files.items():
            target = self.root / relative
            if adopt:
                if target.is_file():
                    hashes[relative] = self._hash(target.read_bytes())
            else:
                hashes[relative] = self._hash(payload)
        value = {
            "version": version, "layout": layout, "hashes": hashes,
            "archive_sha256": self._hash(archive.read_bytes()),
        }
        self.manifest_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _record(self, report: WanxiangUpdateReport) -> WanxiangUpdateReport:
        self.diagnosis_path.write_text(
            json.dumps(report.__dict__, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return report

    @staticmethod
    def _hash(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()
