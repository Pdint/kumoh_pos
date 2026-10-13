# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 설정 — 단일 실행 파일 dist/KumohPOS.exe 생성
#   pyinstaller --noconfirm --clean kumoh_pos.spec

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("assets/kumoh_pos.ico", "assets")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # 사용하지 않는 무거운 표준 모듈 제외 → exe 용량 감소
    excludes=["unittest", "pydoc", "doctest", "xmlrpc", "lib2to3", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KumohPOS",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # 검은 콘솔 창 없이 GUI만 실행
    icon="assets/kumoh_pos.ico",
)
