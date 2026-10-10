import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;

import javax.swing.JButton;
import javax.swing.JFrame;
import javax.swing.JPanel;
import javax.swing.JTextField;
import javax.swing.SwingUtilities;

/**
 * The two boundaries this project documents, as a running application.
 *
 * <p>Written for pyjab-mcp's end-to-end layer, which had no way to test what its own README
 * tells users: that a canvas-drawn interface has no accessible children (so a screenshot is all
 * there is), and that two elements can share an accessible name (which is why a handle carries a
 * position as well as a name).
 *
 * <p><strong>Why the names are set the way they are.</strong> The first version of this file used
 * {@code setName(...)} everywhere and the end-to-end run could not find either element: JAB saw
 * no component called "Canvas panel" and none called "Duplicate field". Two different reasons,
 * both now measured rather than assumed:
 *
 * <ul>
 *   <li>a {@code JPanel} is found by its **accessible name**, which is
 *       {@code getAccessibleContext().setAccessibleName(...)} — {@code setName} does not reach it;
 *   <li>a {@code JTextField} reports its **content** as its accessible name, so setting a name is
 *       beside the point. That is the same rule that made {@code java_set_text} rename the field
 *       it had just typed into, and it is why this file uses two buttons instead.
 * </ul>
 *
 * <p>A button's accessible name is its text, which is the case JAB is best at: two buttons with
 * the same text and different actions, and a status label each of them writes to, so a test can
 * tell which of two same-named handles was actually clicked.
 *
 * <p>Built for Java 8: the end-to-end job runs JDK 17 and the diagnostics matrix runs 8, so
 * staying compatible costs nothing.
 */
public final class BoundaryApp {

    private static final String DEFAULT_TITLE = "pyjab-mcp boundary app";

    public static void main(String[] args) {
        String title = DEFAULT_TITLE;
        for (String argument : args) {
            if (argument.startsWith("--title=")) {
                title = argument.substring("--title=".length());
            }
        }
        final String windowTitle = title;
        SwingUtilities.invokeLater(new Runnable() {
            @Override
            public void run() {
                build(windowTitle);
            }
        });
    }

    private static void build(String title) {
        JFrame frame = new JFrame(title);
        frame.setName("Boundary frame");
        frame.getAccessibleContext().setAccessibleName("Boundary frame");
        frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
        frame.setLayout(new BorderLayout());

        Canvas panel = new Canvas();
        panel.setName("Canvas panel");
        panel.getAccessibleContext().setAccessibleName("Canvas panel");
        panel.setPreferredSize(new Dimension(260, 140));
        frame.add(panel, BorderLayout.CENTER);

        JPanel controls = new JPanel();
        controls.setName("Controls panel");
        controls.getAccessibleContext().setAccessibleName("Controls panel");

        // A text field rather than a label, for a reason the first end-to-end run taught: a
        // text component's text is the one thing this project has already proven it can read,
        // and a label's is not. Its accessible name will be its content, which is the rule that
        // makes java_set_text rename what it types into -- here it is the *evidence*.
        final JTextField status = new JTextField("nothing clicked yet", 22);
        status.setName("Status field");
        status.setEditable(false);

        JButton first = new JButton("Duplicate button");
        first.setName("Duplicate button");
        first.addActionListener(new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent event) {
                status.setText("clicked the first one");
            }
        });

        JButton second = new JButton("Duplicate button");
        second.setName("Duplicate button");
        second.addActionListener(new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent event) {
                status.setText("clicked the second one");
            }
        });

        JButton ordinary = new JButton("Ordinary button");
        ordinary.setName("Ordinary button");

        controls.add(first);
        controls.add(second);
        controls.add(ordinary);
        controls.add(status);
        frame.add(controls, BorderLayout.SOUTH);

        frame.pack();
        frame.setLocation(120, 120);
        frame.setVisible(true);
    }

    /**
     * A panel that paints its own interface, which is the case Java Access Bridge cannot see
     * into: it is one accessible element with no children, whatever the picture shows.
     */
    static final class Canvas extends JPanel {

        private static final long serialVersionUID = 1L;

        @Override
        protected void paintComponent(Graphics graphics) {
            super.paintComponent(graphics);
            Graphics2D g = (Graphics2D) graphics;
            g.setRenderingHint(
                    RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
            g.setColor(new Color(40, 90, 160));
            g.fillRoundRect(16, 16, 96, 60, 18, 18);
            g.setColor(new Color(200, 70, 60));
            g.fillOval(130, 28, 72, 72);
            g.setColor(new Color(60, 60, 60));
            g.drawString("painted, not a component", 20, 116);
        }
    }
}
