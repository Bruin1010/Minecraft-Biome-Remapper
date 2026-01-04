## Changelog

### v1.1
- **Improved error handling and resilience**: The program now continues processing even if individual chunks encounter errors (IndexError, malformed data, etc.). Previously, a single problematic chunk could cause the entire conversion to fail.
- **Enhanced error logging**: Critical errors (IndexError, KeyError, AttributeError) are now always logged with specific region file and chunk index information, making it easier to identify problematic chunks if needed.
- **Better bounds checking**: Added comprehensive bounds checking when accessing biome palette entries and chunk sections to prevent crashes from unexpected data structures.
- **Graceful error recovery**: If a chunk cannot be processed due to malformed data or unexpected structure, the program skips that chunk and continues with the rest of the world. This means you'll still get biome conversions for all the chunks that can be processed, even if a few fail.
- **Important note about errors**: If you see error messages during processing, don't worry! The program is designed to handle errors gracefully. It will continue converting biomes in all the chunks it can process. A few errors here and there are normal and won't prevent the conversion from completing successfully. The error messages are logged for diagnostic purposes, but they don't indicate a failure of the overall conversion process.

### v1.0
- Initial public release
- GUI + Windows EXE build
- Built-in default Terralith → vanilla mapping
- Optional custom mapping INI support


