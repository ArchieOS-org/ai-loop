# Vendor Dependencies

Bundled third-party libraries for offline use. DO NOT load from CDN.

## marked.js v9.1.6

- **Source**: https://cdn.jsdelivr.net/npm/marked@9.1.6/marked.min.js
- **NPM**: https://www.npmjs.com/package/marked/v/9.1.6
- **License**: MIT
- **SHA256**: 6002af63485b043fa60ddaba1b34363b98d2a8b2c63b607004f3a2405a8a053a
- **Downloaded**: 2026-01-04

## DOMPurify v3.0.6

- **Source**: https://cdn.jsdelivr.net/npm/dompurify@3.0.6/dist/purify.min.js
- **NPM**: https://www.npmjs.com/package/dompurify/v/3.0.6
- **License**: Apache-2.0 OR MPL-2.0
- **SHA256**: ea4b09082ca4ba0ae71be6431a097678751d0453b9c52a4d2c7c39a2166ed9fc
- **Downloaded**: 2026-01-04

## Update Procedure

1. Download new version from NPM/CDN
2. Verify integrity: `curl -sL <url> | shasum -a 256`
3. Replace file in this directory
4. Update version + SHA256 in this README
5. Test markdown rendering in the UI
