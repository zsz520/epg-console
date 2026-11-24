"""Prepare NAS bind mounts without taking source files away from their owners."""
import errno
import os
from pathlib import Path
import stat

APP_UID = 10001
APP_GID = 10001


def prepare_data_permissions(root):
    """Grant the application access through its group; retain NAS owner access.

    Inspect every boot: uploading a replacement source can reset its owner and
    mode. Descriptor-relative operations never follow symlinks out of /data.
    """
    root = Path(root)
    changed = 0

    def repair(fd, relative, directory=False):
        nonlocal changed
        info = os.fstat(fd)
        if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1):
            return
        original = stat.S_IMODE(info.st_mode)
        if directory:
            mode = original | stat.S_ISGID | stat.S_IRWXG
            if info.st_uid == APP_UID:
                mode |= stat.S_IRWXU
        else:
            read_only = relative.parts[0] == 'sources' or relative.name == 'secret-key'
            if info.st_uid == APP_UID:
                mode = original | stat.S_IRUSR | (0 if read_only else stat.S_IWUSR)
            else:
                mode = original | stat.S_IRGRP | (0 if read_only else stat.S_IWGRP)
        if info.st_gid != APP_GID:
            os.fchown(fd, -1, APP_GID)
        # chgrp may clear setgid; restore the intended directory mode afterwards.
        if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
            os.fchmod(fd, mode)
        if info.st_gid != APP_GID or original != mode:
            changed += 1

    def failed(exc):
        raise exc

    try:
        for directory, _, files, directory_fd in os.fwalk(root, follow_symlinks=False, onerror=failed):
            relative = Path(directory).relative_to(root)
            repair(directory_fd, relative, directory=True)
            for filename in files:
                try:
                    info = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                except FileNotFoundError:
                    continue  # A task may have finished and removed its temporary file.
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        continue
                    raise
                try:
                    repair(fd, relative / filename)
                finally:
                    os.close(fd)
    except OSError as exc:
        raise RuntimeError(
            f'无法初始化 data 权限（{exc.filename or root}）：{exc.strerror}。'
            '请确认目录以读写方式映射，NAS 允许容器修改文件组和权限。'
        ) from None
    if changed:
        print(f'已自动修复 {changed} 个 data 文件/目录的应用访问权限，保留原所有者。', flush=True)


def check_data_access(root):
    """Explain ACL/root-squash failures before Django starts reading the key."""
    root = Path(root)
    required = [(root, os.R_OK | os.W_OK | os.X_OK),
                (root / 'sources', os.R_OK | os.X_OK),
                (root / 'download', os.R_OK | os.W_OK | os.X_OK)]
    if not os.environ.get('DJANGO_SECRET_KEY'):
        required.append((root / 'secret-key', os.R_OK))
    required.append((root / 'epg.sqlite3', os.R_OK | os.W_OK))
    for path, mode in required:
        if path.exists() and not os.access(path, mode, effective_ids=True):
            raise RuntimeError(
                f'容器用户 {os.getuid()}:{os.getgid()} 无法访问 {path}。'
                '请检查 data 映射是否只读、NAS ACL 或 NFS 权限；不要删除原密钥和数据库。'
            )
