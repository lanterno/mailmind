package app.mailmind.android

import android.app.Application
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import app.mailmind.core.CoreRefusal
import app.mailmind.core.ListView
import app.mailmind.core.MessageRow
import app.mailmind.core.Session
import app.mailmind.core.ViewBatch
import app.mailmind.core.ViewObserver
import app.mailmind.core.buildFixture
import app.mailmind.core.fixtureInbox
import app.mailmind.core.fixtureKey
import java.io.File
import kotlin.concurrent.thread

/**
 * The message list as the screen shows it: the rows the core sent and still says to keep, by position.
 * The core decides which rows those are. Every property here changes on the main thread only.
 */
class MailListModel(application: Application) : AndroidViewModel(application) {
    /** Rows in the whole list. */
    var total by mutableIntStateOf(0)
        private set

    /** The rows held, by position. A position without one shows a placeholder until the core sends it. */
    var rows by mutableStateOf<Map<Int, MessageRow>>(emptyMap())
        private set

    /** The open list, once the mailbox is ready. */
    var view by mutableStateOf<ListView?>(null)
        private set

    /** Why there is no list, in words for the screen. */
    var problem by mutableStateOf<String?>(null)
        private set

    private val main = Handler(Looper.getMainLooper())
    private var session: Session? = null
    private var cleared = false

    init {
        val files = application.filesDir
        val observer = OnMainThread(this)
        // Making and opening the mailbox reads and writes the disk, so it never runs on the main thread.
        thread(name = "mm-open") {
            try {
                val (opened, list) = DemoMailbox.open(files, observer)
                main.post { attach(opened, list) }
            } catch (refusal: CoreRefusal) {
                val words = words(refusal)
                main.post { problem = words }
            } catch (missing: LinkageError) {
                // JNA loads the core when the bindings are first used: a missing library surfaces here, and so does
                // one that will not link (a symbol the device lacks). The log says which, as the screen cannot.
                Log.e(TAG, "The core did not load", missing)
                main.post { problem = "The core is not in this build. Run `just android build`." }
            } catch (failure: Exception) {
                // Only the demo is made here, and its mail is fictional, so the trace may be logged whole.
                Log.e(TAG, "The demo mailbox could not be made", failure)
                main.post { problem = "The demo mailbox could not be made." }
            }
        }
    }

    private fun attach(opened: Session, list: ListView) {
        if (cleared) {
            list.close()
            opened.close()
            return
        }
        session = opened
        total = list.total().toInt()
        view = list
    }

    /** One batch from the core, on the main thread, in the order the core sent them. */
    internal fun take(batch: ViewBatch) {
        val first = batch.heldFirst.toInt()
        val end = first + batch.heldCount.toInt()
        val next = HashMap<Int, MessageRow>(batch.heldCount.toInt())
        // Keep the rows inside the held range and drop the others: the core no longer counts them as held, and
        // sends them again if they come back into range.
        for ((position, row) in rows) {
            if (position in first until end) next[position] = row
        }
        for (range in batch.ranges) {
            var position = range.first.toInt()
            for (row in range.rows) {
                next[position] = row
                position += 1
            }
        }
        total = batch.total.toInt()
        rows = next
    }

    internal fun refuse(refusal: CoreRefusal) {
        problem = words(refusal)
    }

    override fun onCleared() {
        cleared = true
        // Closing the view cancels what it was about to send; then the session's thread ends.
        view?.close()
        session?.close()
        view = null
        session = null
    }
}

private const val TAG = "MailMind"

/** Hears the core on its thread and hands every call to the main thread, keeping their order. */
private class OnMainThread(private val model: MailListModel) : ViewObserver {
    private val main = Handler(Looper.getMainLooper())

    override fun onBatch(batch: ViewBatch) {
        main.post { model.take(batch) }
    }

    override fun onRefusal(refusal: CoreRefusal) {
        main.post { model.refuse(refusal) }
    }
}

/** Step 0 has only a demo: a fictional mailbox, every address at example.test, made by the core on first launch. */
private object DemoMailbox {
    private val MESSAGES: UInt = 100_000u

    fun open(files: File, observer: ViewObserver): Pair<Session, ListView> {
        val database = File(files, "demo-inbox.db")
        val ready = File(files, "demo-inbox.ready")
        if (!ready.exists()) {
            // A first launch cut short leaves half a mailbox. It is fictional, so it is made again from the start.
            for (suffix in listOf("", "-wal", "-shm", "-journal")) {
                File(files, database.name + suffix).delete()
            }
            buildFixture(database.path, MESSAGES)
            ready.createNewFile()
        }
        // One worker: views are served on a thread of the core's own, never on the caller's.
        val session = Session.open(database.path, fixtureKey(), 1u)
        val list = try {
            session.openList(fixtureInbox(), observer)
        } catch (refusal: CoreRefusal) {
            session.close()
            throw refusal
        }
        return session to list
    }
}

/** A refusal in words for the screen. The core never puts a person's data in one. */
private fun words(refusal: CoreRefusal): String = when (refusal) {
    is CoreRefusal.KeyDoesNotFit -> "The key does not open this mailbox."
    is CoreRefusal.NotFound -> "Not found: ${refusal.what}."
    is CoreRefusal.Invalid -> "Not valid: ${refusal.detail}."
    is CoreRefusal.Storage -> "The mailbox could not be read: ${refusal.detail}."
}
