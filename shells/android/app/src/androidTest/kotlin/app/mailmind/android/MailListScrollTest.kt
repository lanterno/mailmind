package app.mailmind.android

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performScrollToIndex
import androidx.lifecycle.ViewModelProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The app opens the demo mailbox (100,000 fictional messages, every address at example.test) and its list scrolls
 * across all of it: a row the core sent, not a placeholder, shows at the top, far down and at the end.
 */
@RunWith(AndroidJUnit4::class)
class MailListScrollTest {
    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    @Test
    fun theListOpensAndScrollsToItsFarEnd() {
        // The activity's own model: the provider hands back the one the screen draws.
        val activity = compose.activity
        val model = compose.runOnUiThread { ViewModelProvider(activity)[MailListModel::class.java] }

        // A first launch makes the mailbox before the list opens, at whatever speed the device's disk has.
        waitForRow(model, 0, FIRST_LAUNCH_MILLIS)
        compose.onNodeWithTag(messageRowTag(0)).assertIsDisplayed()
        val total = compose.runOnUiThread { model.total }
        assertEquals("rows in the demo inbox", MESSAGES, total)

        // A jump: the list moves at once, and the core sends the rows around where it landed.
        compose.onNodeWithTag(MessageListTag).performScrollToIndex(FAR)
        waitForRow(model, FAR, BATCH_MILLIS)
        compose.onNodeWithTag(messageRowTag(FAR)).assertIsDisplayed()

        val last = total - 1
        compose.onNodeWithTag(MessageListTag).performScrollToIndex(last)
        waitForRow(model, last, BATCH_MILLIS)
        compose.onNodeWithTag(messageRowTag(last)).assertIsDisplayed()
    }

    /** Waits for the core's row at [position], failing at once with the screen's words if the app shows a problem. */
    private fun waitForRow(model: MailListModel, position: Int, timeoutMillis: Long) {
        compose.waitUntil("row $position from the core", timeoutMillis) {
            val problem = compose.runOnUiThread { model.problem }
            check(problem == null) { "The app shows a problem instead of its list: $problem" }
            compose.onAllNodesWithTag(messageRowTag(position)).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private companion object {
        /** The demo's size, as `DemoMailbox` asks the core for it. */
        const val MESSAGES = 100_000
        const val FAR = 90_000
        const val FIRST_LAUNCH_MILLIS = 10 * 60_000L
        const val BATCH_MILLIS = 60_000L
    }
}
