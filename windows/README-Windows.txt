門禁紀錄查詢｜Windows 10 / 11（64 位元 x64）

1. 在 ZIP 檔上按滑鼠右鍵，選「解壓縮全部」，將 HAMSAccessQuery 放在本機固定位置。
2. 使用平常能開啟 Y 槽的 Windows 帳號，雙擊 Install-SyncTask.cmd 安裝排程。
3. 雙擊 Sync-Now.cmd 先手動同步一次，以確認來源與權限。
4. 雙擊 Start.cmd。網頁會在預設瀏覽器開啟。使用期間請保持 Start.cmd 的視窗開啟。

排程每週六 16:00（Windows 本機時間）執行。請在排程時保持登入，並讓電腦開著、Y 槽可讀取。Windows 工作排程器內的工作名稱是 HAMSAccessQuery Weekly Sync。若移動程式資料夾，請從新位置再安裝一次排程。

預設從 Y 槽根目錄選擇年份最大的 HAMS_年份.mdb。原始 MDB 複製到 C:\Temp，門禁事件表 PubEvent 轉成 SQLite 查詢索引。轉檔、完整性檢查完成後，網頁才切換到新版本。同步記錄在 C:\Temp\.hams-snapshots\sync.log。以目前約 1.1 GiB 的年度檔估計，同步前建議讓 C 槽保有至少 3 GB 可用空間。來源若有固定產出的 MDB 備份，建議指定該備份。

若 MDB 放在 Y 槽子資料夾，請在 HAMSAccessQuery 資料夾的命令提示字元執行（將「備份」換成實際名稱）：

  Install-SyncTask.cmd "Y:\備份"
  Sync-Now.cmd "Y:\備份"

也可傳入完整的 MDB 路徑，例如 "Y:\備份\HAMS_2026.mdb"。兩個程式的第二個參數可改本機目的資料夾。若有改目的地，請把相同資料夾拖到 Start.cmd 上啟動。

未透過排程轉檔的年度，也可直接把 HAMS_年份.mdb 放到 C:\Temp。網頁在首次使用該年度時會建立索引，存於 %LOCALAPPDATA%\HAMSAccessQuery\cache。套件已包含必要的執行環境。

以「年」切換資料，再依姓名、部門、卡號、裝置名稱、日期時間、門禁點及事件說明查詢。可列印目前結果頁、「報表抬頭」修改報表標題，或將符合已套用條件的全部紀錄匯出為 Excel。
