# HAMS 門禁系統歷史資料瀏覽

在本機瀏覽器查詢 HAMS 門禁系統的歷史刷卡與事件紀錄。程式將年度 Microsoft Access（`.mdb`）檔中的 `PubEvent` 資料表轉成 SQLite 查詢索引，可依年份、時間、姓名、部門、卡號、裝置、門禁點與事件查詢，也可以列印或匯出 Excel。

原始 MDB 以唯讀方式從來源取得。網頁服務只開放本機瀏覽器連線，門禁資料、查詢索引與匯出檔應留在使用端電腦。

## Windows 10／11（x64）

### 安裝並開始同步

1. 將 Windows 免安裝 ZIP 完整解壓縮到固定的本機資料夾。有 ZIP 可直接使用，從 GitHub 下載原始碼時請依下方步驟[建立 Windows 套件](#建立-windows-套件)。
2. 使用檔案總管開啟 `Y:\`，確認目前登入的帳號能讀取年度 MDB，例如 `Y:\HAMS_2026.mdb`。
3. 在解壓縮的 `HAMSAccessQuery` 資料夾雙擊 `Sync-Now.cmd`，先複製並轉檔一次。
4. 雙擊 `Install-SyncTask.cmd`。Windows 工作排程器會建立 `HAMSAccessQuery Weekly Sync`，每週六下午 4 點依 Windows 本機時間執行。排程時間請讓電腦開著、帳號保持登入，並確保 Y 槽可讀取。
5. 雙擊 `Start.cmd` 開啟瀏覽器。查詢期間保持 `Start.cmd` 的視窗開啟。預設網址是 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)，實際網址會顯示在該視窗。

每次同步會選擇 Y 槽根目錄中年份最大的 `HAMS_年份.mdb`，在 `C:\Temp` 保留一份 MDB，再發布經過完整性檢查的 SQLite。網頁會顯示該年度的最近成功同步時間，開著的頁面會自動載入新版本。轉檔失敗時，網頁仍可使用上一版。同步紀錄在 `C:\Temp\.hams-snapshots\sync.log`。

### 自訂來源或手動放入 MDB

如果年度檔在子資料夾，請在解壓縮的 `HAMSAccessQuery` 資料夾開啟命令提示字元，將範例中的 `備份` 換成實際資料夾名稱。

```bat
Sync-Now.cmd "Y:\備份"
Install-SyncTask.cmd "Y:\備份"
```

也可指定完整檔名，例如 `"Y:\備份\HAMS_2026.mdb"`。來源系統若有產出固定的 MDB 備份，建議指定該備份，避免複製時原檔仍在寫入。兩個指令可用第二個參數指定不同的本機資料夾。請將同一個資料夾拖到 `Start.cmd` 上開啟網頁。

只想查詢本機或往年資料時，可直接把 `HAMS_年份.mdb` 放到 `C:\Temp` 後執行 `Start.cmd`。程式會在首次查詢該年度時建立索引。以約 1.1 GiB 的年度 MDB 為例，同步前建議至少保留 3 GB 可用空間。

### 建立 Windows 套件

建置環境需要 Python 3.9 以上、JDK 11 以上（需有 `javac`）及網路連線。下載或複製此專案後，在專案根目錄執行下列指令。在 macOS 請使用 `python3`。

```sh
python windows/package_windows.py
```

完成後，`dist` 資料夾會生成 `門禁紀錄查詢-Windows-x64.zip`。ZIP 內含 Python 和 MDB 讀取工具，Windows 使用端解壓後即可啟動。

## macOS 本機查詢

1. 安裝 Python 3.9 以上，並以 Homebrew 執行 `brew install mdbtools`。
2. 在專案根目錄放入 `HAMS_2026.mdb` 或單一年度 MDB。
3. 雙擊 `啟動門禁查詢.command`，或從專案根目錄執行 `python3 server.py --open`。
4. 第一次啟動請等候 SQLite 索引建立。使用完畢可在終端機按 `Control+C` 結束服務。

## 查詢與報表

1. 在網頁上方選擇「年」，按該欄旁的「查詢」載入年度紀錄。
2. 輸入姓名、卡號或其他查詢條件，再按「查詢條件」區塊下方的「查詢」。可以設定每頁顯示筆數，或按「清除條件」重新查詢。
3. 「報表抬頭」可修改報表標題，「列印」會列印目前頁面。「匯出 Excel」會下載所有符合目前查詢條件的紀錄，匯出整年資料時請等候檔案完成。
