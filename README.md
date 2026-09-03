# Growi_Export_Import

---

Export data from Growi, and import this data.

## Overview

- Specified page and below --( **Export** )--> Specified directory below
- Specified page below <--( **Import** )-- Specified directory and below 
- Items

| **#** | **Item**   | **Export** | **Import** | **Export Format** | **Notes**                                                    |
| :---: | ---------- | :--------: | :--------: | :---------------: | ------------------------------------------------------------ |
| 1     | Page       | Yes        | Yes        | JSON<br>Markdown     | `page.json`, `<page name>.md` <br>Supports WIP                                          |
| 2     | Attachment | Yes        | Yes        | JSON<br>Attachment   | `attachments.json`, Attachment files                         |
| 3     | Comment    | Yes        | Yes        | JSON              | `comments.json`<br>:warning: Import: Comments are posted (restored) under the import user, <br>with the original author clearly noted at the beginning of the comment. |
| 4     | Tag        | Yes        | Yes        | JSON              | `tags.json`                                                  |
| 5     | Bookmark &#x1F516; | Yes        | Yes        | JSON              | `bookmarks.json`                                             |
| 6     | In-App-Notification &#x1F514; | -          | -          |                   |                                                              |
| 7     | Liker &#x2661; | -          | -          |                   |                                                              |
| 8     | Seen &#x1F463; | -          | -          |                   |                                                              |
| 9     | Share Links | -          | -          |                   |                                                              |
| 10    | Update History | -          | -          |                   |                                                              |



### Notes

- Growi paths cannot be used directly as file paths during export. Characters that are invalid in file paths are replaced with `_`.
- The default value for `NORMALIZATION_FORM` is **NFC**. Growi paths and file paths are "composed".
- Operating systems impose limits on the maximum length of file paths. If a path exceeds this limit, the item will be logged and skipped.
  - Info: On Windows, the app treats 260 characters as the maximum limit. If you wish to relax this limit, please modify the code.

## Background & Reason for Creation

- Created with the help of Google Gemini because GROWI's **data archive** and **data import** functions were not working properly.
  - **Issue 1:** Data archiving fails when a page has too many history entries.
  - **Issue 2:** Recreated the GROWI application and attempted a data import, but it failed.
- **Estimated Cause:** Since the free plan of the GROWI cloud service is being used, the allocated memory for the virtual machine is likely too small, causing data extraction/expansion to fail.

## Execution Procedure

1. Obtaining an Access-Token
    1. Log in to GROWI, click the **user icon** at the bottom left of the screen, and select **"Settings"** from the menu.
    2. Click **"API Settings"** within the settings menu.
    3. Click **Access token settings > New**. :warning: Do not use the API Token settings.
    4. Set the **Expiration date, Description, and Scope**, then click **Create token**.
    5. The **Token** will be displayed only once at this time, so copy it and **save it securely**.

    
    
2. Setting up the Execution Environment  
    The `requests` library is required to run the script. Run the following command in a terminal or similar environment to install it.

    ```bash
    pip install requests
    ```



3. Creating the Configuration File (~.ini)

   - Template

        ```bash
        # GROWI Export

        GROWI_URL       = https://<user name>.growi.cloud
        GROWI_PATH      = /user/<user name>
        ACCESS_TOKEN    = <your access token here>
        ;EXPORT_DIR      = ./growi_export (default)
        ;NORMALIZATION_FORM = NFC (default) | NFD | raw
        ```
     
        ```bash
        # GROWI Import

        GROWI_URL       = https://<user name>.growi.cloud
        GROWI_PATH      = /user/<user name>
        ACCESS_TOKEN    = <your access token here>
        ;EXPORT_DIR      = ./growi_export (default)
        ;NORMALIZATION_FORM = NFC (default) | NFD | raw
     
        ;IMPORT_COMMENTS = True (default) | False
        ;IMPORT_BOOKMARK = True (default) | False
        ```

   - Descriptions
       - **GROWI_URL**: Sets the Growi URL.
           - Example for [Online Service](https://growi.org): `https://foo.growi.cloud`
           - Example for [On-premises](https://github.com/growilabs/growi-docker-compose): `http://localhost:3000`
       - **GROWI_PATH**: Specifies the target page.
           - Exporting: The specified page and its subpages are exported to `EXPORT_DIR`.
           - Importing: The contents of `EXPORT_DIR` and its subdirectories are imported under the specified page.
           - Example: `/user/foo`
       - **ACCESS_TOKEN**: Specifies the access token obtained earlier.
           - Exporting:
               - No access token specified: Only public pages are exported. The exporting user is recorded as **unknown**. 
               - Scope is `read:all`: The exporting user is recorded as **unknown** . (username cannot be determined)
               - Scope is `write:all`: The exporting user is recorded.
           - Importing: Specify an access token with the `write:all` scope.
       - **EXPORT_DIR**: Specifies the target directory.
           - Exporting: Specifies the destination directory for the export.
           - Importing: Specifies the directory to import from.
           - Default value: `./growi_export`
       - **NORMALIZATION_FORM**: Specifies the [Unicode normalization form](https://en.wikipedia.org/wiki/Unicode_equivalence#Normal_forms). Apply only to the file/page path.
           - **NFC** (Default): Normalization Form Canonical Composition
               - Example: Converts `e` + `́` into `é` (Composition).
           - **NFD**: Normalization Form Canonical Decomposition
               - Example: Converts `é` into `e` + `́` (Decomposition).
           - **raw**: No conversion. (Note: Any value other than the above two is treated as "raw")
       - **IMPORT_COMMENTS**:　Specify the import of comments. (import only)
         - True (Default)
         - False
       - **IMPORT_BOOKMARK**:　Specify the import of bookmarks. (import only)
         - True (Default)
         - False

  - Configuration Example:

    ```bash
    # GROWI Export
    
    GROWI_URL    = https://foo.growi.cloud
    GROWI_PATH   = /user/foo/bar/baz
    ACCESS_TOKEN = b24171................................................fd69f990f3
    
    ;EXPORT_DIR      = ./growi_export (default)
    ;NORMALIZATION_FORM = NFC (default) | NFD | raw
    ```

4. Running the Script

   - Commands
       ```bash
       # Export
       # make Growi_export.ini
       python Growi_export.py
    
       # Import
       # make Growi_import.ini
       python Growi_import.py
       ```

   - Help

        ```bash
        > python Growi_export.py --help
        usage: Growi_export.py [-h] [-c CONFIG]

        GROWI Markdown Export.

        optional arguments:
          -h, --help            show this help message and exit
          -c CONFIG, --config CONFIG
                                Path to the configuration file (default: ./Growi_export.ini)

        > python Growi_import.py --help
        usage: Growi_import.py [-h] [-c CONFIG]

        GROWI Markdown Import.

        optional arguments:
          -h, --help            show this help message and exit
          -c CONFIG, --config CONFIG
                                Path to the configuration file (default: ./Growi_import.ini)
        ```


5. Cleanup  
   **Delete the access token**.  
   Refer to "Obtaining an Access-Token" for instructions on how to manage tokens.


## About Maximum File Path Length

| **OS**  | **Limit (Standard)** | **Special Notes & Supplementary Information**                                                                                              |
| :-----: | :------------------: |--------------------------------------------------------------------------------------------------------------------------------------------|
| Windows | 260 chars            | - Traditional Win32 API limitation. <br>- **Can be extended up to 32,767 characters** via configuration or application support.             |
| macOS   | 1,024 chars          | - Depends on file system specifications (APFS / HFS+). <br>- Individual file and folder names are limited to 255 characters max.            |
| Linux   | 4,096 chars          | - Standard kernel limitation (defined as 4096 in `linux/limits.h`). <br>- Individual file and folder names are limited to 255 characters max. |



### How to Extend the Limit in Windows ( Response from Gemini below )

In Windows 10 (Version 1607 and later) and Windows 11, you can lift the traditional 260-character limit (`MAX_PATH`) and enable long paths of up to **32,767 characters** by changing system settings.

- Using PowerShell (Easiest)

    Launch PowerShell with administrator privileges and run the following single command to modify the registry:
    
    PowerShell
    ```
    New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
    ```

    A system reboot is required after execution.

