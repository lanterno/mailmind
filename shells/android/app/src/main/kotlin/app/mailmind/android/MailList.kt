package app.mailmind.android

import android.text.format.DateFormat
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.mailmind.core.ListView
import app.mailmind.core.MessageRow
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import kotlinx.coroutines.flow.distinctUntilChanged

/** Every row is the same height, so the list has its full length before its rows arrive. */
private val RowHeight = 80.dp

@Composable
fun MailListScreen(model: MailListModel) {
    Surface(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize().safeDrawingPadding()) {
            Text(
                text = "Inbox",
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            )
            val view = model.view
            val problem = model.problem
            when {
                problem != null -> Notice(problem)
                view == null -> Notice("Opening the demo mailbox. The first launch makes 100,000 fictional messages.")
                else -> MessageList(model, view)
            }
        }
    }
}

@Composable
private fun MessageList(model: MailListModel, view: ListView) {
    val state = rememberLazyListState()
    // Tell the core which rows are on screen whenever that changes. Its answer comes back through the model.
    LaunchedEffect(view, state) {
        snapshotFlow { state.firstVisibleItemIndex to state.layoutInfo.visibleItemsInfo.size }
            .distinctUntilChanged()
            .collect { window -> view.setVisible(window.first.toUInt(), window.second.toUInt()) }
    }
    val dates = remember { DateLabels() }
    LazyColumn(state = state, modifier = Modifier.fillMaxSize()) {
        items(count = model.total) { position ->
            val row = model.rows[position]
            if (row == null) PlaceholderRow() else MessageRowView(row, dates)
        }
    }
}

@Composable
private fun MessageRowView(row: MessageRow, dates: DateLabels) {
    val weight = if (row.unread) FontWeight.SemiBold else FontWeight.Normal
    val quiet = MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(RowHeight)
            .padding(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Box(
            modifier = Modifier
                .padding(top = 7.dp)
                .size(8.dp)
                .background(if (row.unread) MaterialTheme.colorScheme.primary else Color.Transparent, CircleShape),
        )
        Spacer(modifier = Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = row.sender,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = weight,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (row.attachment) {
                    Icon(
                        imageVector = Paperclip,
                        contentDescription = "Attachment",
                        modifier = Modifier.size(16.dp),
                        tint = quiet,
                    )
                }
                if (row.flagged) {
                    Icon(
                        imageVector = Flag,
                        contentDescription = "Flagged",
                        modifier = Modifier.size(16.dp),
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    text = dates.label(row.date),
                    style = MaterialTheme.typography.labelMedium,
                    color = quiet,
                    maxLines = 1,
                )
            }
            Text(
                text = row.subject,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = weight,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = row.snippet,
                style = MaterialTheme.typography.bodySmall,
                color = quiet,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** A row the core has not sent yet. The shell draws only its outline: what it holds is the core's to say. */
@Composable
private fun PlaceholderRow() {
    val shade = MaterialTheme.colorScheme.surfaceVariant
    val bar = RoundedCornerShape(4.dp)
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .height(RowHeight)
            .padding(start = 34.dp, end = 16.dp, top = 12.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(modifier = Modifier.fillMaxWidth(0.4f).height(12.dp).background(shade, bar))
        Box(modifier = Modifier.fillMaxWidth(0.7f).height(10.dp).background(shade, bar))
        Box(modifier = Modifier.fillMaxWidth(0.9f).height(10.dp).background(shade, bar))
    }
}

@Composable
private fun Notice(words: String) {
    Text(
        text = words,
        style = MaterialTheme.typography.bodyLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(16.dp),
    )
}

/**
 * Dates as the list shows them, in the phone's zone and language: the time for today, day and month for this
 * year, the short date before that. The core sends seconds since 1970; how they read is the shell's to choose.
 */
private class DateLabels {
    private val zone = ZoneId.systemDefault()
    private val locale = Locale.getDefault()
    private val today = LocalDate.now(zone)
    private val time = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale)
    private val dayAndMonth = DateTimeFormatter.ofPattern(DateFormat.getBestDateTimePattern(locale, "MMMd"), locale)
    private val date = DateTimeFormatter.ofLocalizedDate(FormatStyle.SHORT).withLocale(locale)

    fun label(seconds: Long): String {
        if (seconds == 0L) return ""
        val at = Instant.ofEpochSecond(seconds).atZone(zone)
        val day = at.toLocalDate()
        return when {
            day == today -> time.format(at)
            day.year == today.year -> dayAndMonth.format(at)
            else -> date.format(at)
        }
    }
}

// Material 3 ships no icon set, and these two are all the list needs: drawn here rather than adding one.

private val Flag: ImageVector = ImageVector.Builder(
    name = "Flag",
    defaultWidth = 24.dp,
    defaultHeight = 24.dp,
    viewportWidth = 24f,
    viewportHeight = 24f,
).addPath(
    pathData = addPathNodes("M5 3h2v18H5z M8 4h11l-3 4.5 3 4.5H8z"),
    fill = SolidColor(Color.Black),
).build()

private val Paperclip: ImageVector = ImageVector.Builder(
    name = "Paperclip",
    defaultWidth = 24.dp,
    defaultHeight = 24.dp,
    viewportWidth = 24f,
    viewportHeight = 24f,
).addPath(
    pathData = addPathNodes("M16 7v9a4 4 0 0 1-8 0V6a2.5 2.5 0 0 1 5 0v9.5a1 1 0 0 1-2 0V8"),
    stroke = SolidColor(Color.Black),
    strokeLineWidth = 1.8f,
    strokeLineCap = StrokeCap.Round,
    strokeLineJoin = StrokeJoin.Round,
).build()
