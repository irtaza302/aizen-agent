#!/bin/bash
set -e

echo "🚀 Starting the Aether release process..."

echo "----------------------------------------"
echo "📦 1. Building and Publishing to PyPI"
echo "----------------------------------------"
rm -rf dist/ build/ *.egg-info
python3 setup.py sdist bdist_wheel
if command -v twine &> /dev/null; then
    echo "Uploading to PyPI..."
    twine upload dist/* || echo "⚠️ Twine upload failed or skipped. (Did you log in?)"
else
    echo "⚠️ Twine not found. Skipping PyPI upload."
    echo "   (Install it with: pip install twine)"
fi

echo ""
echo "----------------------------------------"
echo "📦 2. Publishing to NPM"
echo "----------------------------------------"
if command -v npm &> /dev/null; then
    cd npm-package
    npm publish || echo "⚠️ NPM publish failed or skipped. (Did you log in?)"
    cd ..
else
    echo "⚠️ NPM not found. Skipping."
fi

echo ""
echo "----------------------------------------"
echo "📦 3. Building macOS Binary (for Homebrew/Github)"
echo "----------------------------------------"
if command -v pyinstaller &> /dev/null; then
    pyinstaller aether.spec
    cd dist
    tar -czvf aether-macos.tar.gz aether
    echo "✅ macOS binary built at dist/aether-macos.tar.gz"
    echo "   (To update Homebrew, calculate the shasum with 'shasum -a 256 dist/aether-macos.tar.gz')"
    echo "   (Then update the sha256 and url in homebrew-aether/Formula/aether.rb)"
    cd ..
else
    echo "⚠️ PyInstaller not found. Skipping binary build."
    echo "   (Install it with: pip install pyinstaller)"
fi

echo ""
echo "🎉 Done! Everything has been prepared/published."
