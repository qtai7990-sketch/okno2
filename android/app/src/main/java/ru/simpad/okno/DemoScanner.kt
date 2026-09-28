package ru.simpad.okno

import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import kotlin.random.Random

/** Демо-сканер для обучения. Рабочий сервер такие данные отклоняет (source = demo). */
class DemoScanner : DocumentScanner {

    private val samples = listOf(
        listOf("RAKHIMOV", "FARRUKH", "Рахимов Фаррух", "TJK", "Таджикистан", "401234567", "1994-03-12", "M", "2032-03-11", "false"),
        listOf("KARIMOV", "JASUR", "Каримов Жасур", "UZB", "Узбекистан", "FA5512907", "1992-01-17", "M", "2033-01-16", "false"),
        listOf("TOKTOGULOV", "EMIL", "Токтогулов Эмиль", "KGZ", "Кыргызстан", "AC7700312", "2000-04-05", "M", "2030-04-04", "true")
    )

    override fun scan(kind: String, done: (JSONObject) -> Unit) {
        when (kind) {
            "passport" -> {
                val s = samples[Random.nextInt(samples.size)]
                val data = JSONObject()
                    .put("surname", s[0]).put("given", s[1]).put("cyr", s[2])
                    .put("country", s[3]).put("country_name", s[4]).put("number", s[5])
                    .put("dob", s[6]).put("sex", s[7]).put("expiry", s[8])
                    .put("has_ru_text", s[9].toBoolean())
                    .put("source", "demo")
                    .put("checks", JSONArray().put("демо‑данные"))
                done(scanOk(data))
            }
            "migcard" -> {
                val today = LocalDate.now()
                val data = JSONObject()
                    .put("series", "4626")
                    .put("number", (1000000 + Random.nextInt(8999999)).toString())
                    .put("entry_date", today.toString())
                    .put("kpp", "Жуковский (аэропорт)")
                    .put("purpose", "Работа")
                    .put("stay_until", today.plusDays(90).toString())
                done(scanOk(data))
            }
            "imei" -> {
                val digits = (1..13).joinToString("") { Random.nextInt(10).toString() }
                done(scanOk(JSONObject().put("imei", "35$digits")))
            }
            "ruid" -> done(scanOk(JSONObject().put("ok", true)))
            else -> done(scanFail("Неизвестный тип сканирования: $kind"))
        }
    }
}
