#!/usr/bin/env python3

import contextlib
import enum
import os
import re
import shlex
import subprocess
import sys
from typing import (Dict, Iterator, Mapping, NamedTuple, Optional, Sequence,
                    Tuple)


class PartitionType(enum.Enum):
    LINUX_HOME = '8302'
    LINUX_X86_64_ROOT = '8304'
    EFI_SYSTEM = 'ef00'

    @property
    def type_id(self) -> str:
        return self.value


class FileSystemType(enum.Enum):
    BTRFS = ('btrfs', ['mkfs.btrfs', '-f'])
    EXT4 = ('ext4', ['mkfs.ext4'])
    FAT32 = ('vfat', ['mkfs.fat', '-F32'])

    @property
    def name(self) -> str:
        return self.value[0]

    @property
    def formatter(self) -> Sequence[str]:
        return self.value[1]


class DesktopType(enum.Enum):
    KDE = (['alacritty', 'dolphin', 'plasma-desktop', 'sddm', 'xorg-server'], 'sddm')

    @property
    def packages(self) -> Sequence[str]:
        return self.value[0]

    @property
    def display_manager(self) -> str:
        return self.value[1]


class Partition(NamedTuple):
    size: Optional[int]  # Unit: MiB.
    partition_type: str
    file_system: FileSystemType
    mount_point: str


class MountSpec(NamedTuple):
    disk: str
    partition_id: int
    file_system_name: str

    @property
    def partition(self) -> str:
        return f'{self.disk}{self.partition_id}'


class MountSpecs(NamedTuple):
    root: MountSpec
    boot: Optional[MountSpec]
    home: Optional[MountSpec]


class Configuration(NamedTuple):
    partitions: Mapping[str, Sequence[Partition]]
    packages: Sequence[str]
    time_zone: str
    locale: str
    host_name: str
    user_name: str
    user_full_name: str
    user_password: str
    mirrors: Sequence[str]
    desktop: Optional[DesktopType]
    drivers: Sequence[str]


def _get_configuration():
    return Configuration(
        partitions={
            '/dev/sda': [
                Partition(partition_type=PartitionType.EFI_SYSTEM,
                          size=256,
                          file_system=FileSystemType.FAT32,
                          mount_point='/boot'),
                Partition(partition_type=PartitionType.LINUX_X86_64_ROOT,
                          size=10240,
                          file_system=FileSystemType.EXT4,
                          mount_point='/'),
                Partition(partition_type=PartitionType.LINUX_HOME,
                          size=None,
                          file_system=FileSystemType.BTRFS,
                          mount_point='/home'),
            ]
        },
        packages=['linux', 'pacman', 'sed', 'sudo'],
        time_zone='Asia/Shanghai',
        locale='en_US.UTF-8',
        host_name='EFanZh-PC-Arch',
        user_name='efanzh',
        user_full_name='EFanZh',
        user_password='1234',
        mirrors=['https://mirrors.tuna.tsinghua.edu.cn/archlinux',
                 'https://mirrors.ustc.edu.cn/archlinux'],
        desktop=DesktopType.KDE,
        drivers=['xf86-video-fbdev']
    )


def _write_output(buffer, output):
    if output:
        buffer.write(output)

        if not output.endswith(b'\n'):
            buffer.write('/\n'.encode())

        buffer.flush()


def _run(*args: Sequence[str]):
    separator_length = 120

    print('=' * separator_length)
    print('=>', *map(shlex.quote, args), '... ', end='', flush=True)

    try:
        subprocess.run(args=args,
                       stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE,
                       check=True)
    except subprocess.CalledProcessError as error:
        print(f'Failed ({error.returncode})', flush=True)
        print('-' * separator_length, flush=True)
        _write_output(sys.stdout.buffer, error.stdout)
        print('-' * separator_length, flush=True)
        _write_output(sys.stdout.buffer, error.stderr)
        print('-' * separator_length, flush=True)

        raise
    except KeyboardInterrupt:
        print(flush=True)

        raise
    else:
        print('OK', flush=True)


def _write_line_to_file(content: str, file: str):
    _run('sh', '-c', ' '.join([*map(shlex.quote, ['printf', r'%s\n', content]), '>', shlex.quote(file)]))


def _append_line_to_file(content: str, file: str):
    _run('sh', '-c', ' '.join([*map(shlex.quote, ['printf', r'%s\n', content]), '>>', shlex.quote(file)]))


def _partition(partitions: Mapping[str, Sequence[Partition]]):
    # TODO: Support flexible partition schemes.

    for disk, partition_items in partitions.items():
        _run('sgdisk', '-Z', disk)

        for i, partition_item in enumerate(partition_items, 1):
            if partition_item.size:
                _run('sgdisk', f'-n={i}:0:+{partition_item.size}M', disk)
            else:
                _run('sgdisk', f'-n={i}:0:0', disk)

            _run('sgdisk', f'-t={i}:{partition_item.partition_type.type_id}', disk)
            _run(*partition_item.file_system.formatter, f'{disk}{i}')


@contextlib.contextmanager
def _do_mount(mount_specs: Iterator[Tuple[str, str]]):
    try:
        target, source = next(mount_specs)
    except StopIteration:
        yield
    else:
        _run('mkdir', '-p', target)
        _run('mount', source, target)

        try:
            with _do_mount(mount_specs):
                yield
        finally:
            _run('umount', target)


def _mount_filesystems(partitions: Mapping[str, Sequence[Partition]], root: str):
    root = root.rstrip('/')

    mount_specs = sorted((root + partition_item.mount_point.rstrip('/'), f'{disk}{i}')
                         for disk, partition_items in partitions.items()
                         for i, partition_item in enumerate(partition_items, 1))

    return _do_mount(iter(mount_specs))


def _configure_mirrors(mirrors: Sequence[str]):
    _write_line_to_file('\n'.join(f'Server = {mirror}/$repo/os/$arch' for mirror in mirrors),
                        '/etc/pacman.d/mirrorlist')


def _install_packages(packages: Sequence[str], root: str):
    _run('pacstrap', root, *packages)


def _get_mount_specs(partitions: Mapping[str, Sequence[Partition]]) -> MountSpecs:
    mount_points: Dict[str, str] = {}

    for disk, partition_items in partitions.items():
        for i, partition_item in enumerate(partition_items, 1):
            mount_point = partition_item.mount_point.rstrip('/')

            if mount_point in mount_points:
                raise ValueError(f'Mount point “{mount_point}” already exists.')

            mount_points[mount_point] = MountSpec(disk=disk,
                                                  partition_id=i,
                                                  file_system_name=partition_item.file_system.name)

    return MountSpecs(root=mount_points[''],
                      boot=mount_points.get('/boot'),
                      home=mount_points.get('/home'))


def _write_fstab(mount_specs: MountSpecs, root: str):
    lines = []

    for mount_target, mount_spec in [('/boot', mount_specs.boot),
                                     ('/home', mount_specs.home)]:
        if mount_spec and mount_spec.disk != mount_specs.root.disk:
            lines.append((mount_spec.partition, mount_target, mount_spec.file_system_name, 'defaults', 0, 0))

    for line in lines:
        _append_line_to_file('  '.join(map(str, line)), os.path.join(root, 'etc/fstab'))


def _get_default_network_interface():
    default_route_regex = re.compile(r'default(?:\s.*)*\sdev\s+(\S+)')

    for line in subprocess.check_output(args=['ip', 'route'], universal_newlines=True).splitlines():
        match = default_route_regex.match(line)

        if match:
            return match[1]

    raise RuntimeError('Unable to get default network interface')


def _link_service(source, target, root):
    full_target_path = os.path.join(root, 'etc/systemd/system', target)

    _run('mkdir', '-p', os.path.dirname(full_target_path))
    _run('ln', '-s', os.path.join('/usr/lib/systemd/system', source), full_target_path)


def _configure_network(root):
    # Configure network.

    default_interface = _get_default_network_interface()

    network_configuration = [
        '[Match]',
        f'Name={default_interface}',
        '',
        '[Network]',
        'DHCP=ipv4'
    ]

    network_configuration_path = os.path.join(root, f'etc/systemd/network/20-{default_interface}.network')

    for line in network_configuration:
        _append_line_to_file(line, network_configuration_path)

    # Enable systemd-networkd.

    _link_service('systemd-networkd.service', 'dbus-org.freedesktop.network1.service', root)
    _link_service('systemd-networkd.service', 'multi-user.target.wants/systemd-networkd.service', root)
    _link_service('systemd-networkd.socket', 'sockets.target.wants/systemd-networkd.socket', root)

    _link_service('systemd-networkd-wait-online.service',
                  'network-online.target.wants/systemd-networkd-wait-online.service', root)

    # Configure DNS.

    _run('ln', '-fs', '/run/systemd/resolve/stub-resolv.conf', os.path.join(root, 'etc/resolv.conf'))

    # Enable systemd-resolved.

    _link_service('systemd-resolved.service', 'dbus-org.freedesktop.resolve1.service', root)
    _link_service('systemd-resolved.service', 'multi-user.target.wants/systemd-resolved.service', root)

    # Enable systemd-timesyncd.

    _link_service('systemd-timesyncd.service', 'dbus-org.freedesktop.timesync1.service', root)
    _link_service('systemd-timesyncd.service', 'sysinit.target.wants/systemd-timesyncd.service', root)


def _configure_system(configuration: Configuration, root: str):
    # File system.

    mount_specs = _get_mount_specs(configuration.partitions)

    _write_fstab(mount_specs, root)

    # Time zone.

    _run('ln',
         '-s',
         os.path.join('../usr/share/zoneinfo', configuration.time_zone),
         os.path.join(root, 'etc/localtime'))

    # Locale.

    _run('sed', '-E', '-i', fr's/^#({re.escape(configuration.locale)}\s.*)/\1/', os.path.join(root, 'etc/locale.gen'))
    _run('arch-chroot', root, 'locale-gen')
    _write_line_to_file(f'LANG={configuration.locale}', os.path.join(root, 'etc/locale.conf'))

    # Host name.

    _write_line_to_file(configuration.host_name, os.path.join(root, 'etc/hostname'))

    # Network.

    _configure_network(root)

    # Boot.

    _run('sed', '-i', r'/^HOOKS=/ s/\budev\b/systemd/', os.path.join(root, 'etc/mkinitcpio.conf'))
    _run('arch-chroot', root, 'mkinitcpio', '-P')
    _run('arch-chroot', root, 'bootctl', 'install')

    arch_linux_entry = [
        'title    Arch Linux',
        'linux    /vmlinuz-linux',
        'initrd   /initramfs-linux.img',
        'options  rw'
    ]

    for arch_linux_entry_line in arch_linux_entry:
        _append_line_to_file(arch_linux_entry_line, os.path.join(root, 'boot/loader/entries/arch-linux.conf'))

    # Create user.

    _run('arch-chroot', root, 'useradd', '-c', configuration.user_full_name, '-m', configuration.user_name)
    _run('arch-chroot', root, 'usermod', '-aG', 'wheel', configuration.user_name)
    _run('arch-chroot', root, 'sed', '-E', '-i', r's/^#\s*(%wheel.*NOPASSWD.*)/\1/', '/etc/sudoers')

    password_line = f'{configuration.user_name}:{configuration.user_password}'

    _run('arch-chroot', root, 'sh', '-c', f"printf '%s' {shlex.quote(password_line)} | chpasswd")

    # Configure desktop.

    if configuration.desktop:
        _link_service(f'{configuration.desktop.display_manager}.service', 'display-manager.service', root)


def main(configuration: Configuration):
    root = '/mnt'

    _partition(configuration.partitions)

    with _mount_filesystems(configuration.partitions, root):
        _configure_mirrors(configuration.mirrors)

        all_packages = list(configuration.packages)
        all_packages.extend(configuration.drivers)

        if configuration.desktop:
            all_packages.extend(configuration.desktop.packages)

        _install_packages(all_packages, root)

        _configure_system(configuration, root)


if __name__ == '__main__':
    main(_get_configuration())
