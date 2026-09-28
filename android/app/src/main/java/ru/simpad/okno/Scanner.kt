package ru.simpad.okno

import org.json.JSONObject

/** Сканер документов. kind: passport | migcard | imei | ruid. */
interface DocumentScanner {
    fun scan(kind: String, done: (JSONObject) -> Unit)
}

fun scanOk(data: JSONObject): JSONObject = JSONObject().put("ok", true).put("data", data)

fun scanFail(message: String): JSONObject = JSONObject().put("ok", false).put("error", message)
