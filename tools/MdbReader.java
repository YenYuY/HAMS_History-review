import com.healthmarketscience.jackcess.Database;
import com.healthmarketscience.jackcess.DatabaseBuilder;
import com.healthmarketscience.jackcess.Row;
import com.healthmarketscience.jackcess.Table;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;

/** Exports the columns used by the local indexer without modifying the MDB. */
public final class MdbReader {
    private static final List<String> COLUMNS = List.of(
        "eventDate", "eventTime", "deviceName", "doorName", "deptName",
        "personName", "personID", "eventCard", "eventName", "NewEventCode_Name"
    );

    private static void field(BufferedWriter output, Object value) throws IOException {
        output.write('"');
        if (value != null) {
            output.write(value.toString().replace("\"", "\"\""));
        }
        output.write('"');
    }

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            throw new IllegalArgumentException("Expected an MDB path and table name");
        }
        try (Database database = new DatabaseBuilder(Path.of(args[0])).setReadOnly(true).open();
             BufferedWriter output = new BufferedWriter(new OutputStreamWriter(System.out, StandardCharsets.UTF_8), 65536)) {
            Table table = database.getTable(args[1]);
            if (table == null) {
                throw new IOException("Missing event table");
            }
            var available = new HashMap<String, String>();
            for (var column : table.getColumns()) {
                available.put(column.getName().toLowerCase(Locale.ROOT), column.getName());
            }
            var actualColumns = new ArrayList<String>();
            var selectedColumns = new ArrayList<String>();
            for (int i = 0; i < COLUMNS.size(); i++) {
                String name = COLUMNS.get(i);
                String actual = available.get(name.toLowerCase(Locale.ROOT));
                if (actual == null && i < COLUMNS.size() - 2) {
                    throw new IOException("Missing required event column: " + name);
                }
                actualColumns.add(actual);
                if (actual != null) {
                    selectedColumns.add(actual);
                }
            }
            if (actualColumns.get(COLUMNS.size() - 2) == null && actualColumns.get(COLUMNS.size() - 1) == null) {
                throw new IOException("Missing event description columns");
            }

            output.write(String.join(",", COLUMNS));
            output.write('\n');
            for (Row row : table.newCursor().toCursor().newIterable().setColumnNames(selectedColumns)) {
                for (int i = 0; i < actualColumns.size(); i++) {
                    if (i > 0) {
                        output.write(',');
                    }
                    String actual = actualColumns.get(i);
                    field(output, actual == null ? null : row.get(actual));
                }
                output.write('\n');
            }
        }
        if (System.out.checkError()) {
            throw new IOException("Could not write event stream");
        }
    }
}
